"""Adapter z lab5-support/demo/shared/messaging.py, dostosowany do magazynu.

Polaczenia sa otwierane dopiero podczas operacji. Sam dostep do adaptera
nie uzaleznia uruchomienia HTTP od RabbitMQ.
"""
from collections import deque
from contextlib import contextmanager
import json
import os
from threading import RLock

import pika


class BrokerUnavailableError(RuntimeError):
    pass


class InMemoryEventBroker:
    """Odpowiednik brokera do testow bez sieci; nieudana obsluga zachowuje wiadomosc."""

    def __init__(self, down=False):
        self.down = down
        self.published = []
        self._queues = {}
        self._bindings = {}
        self._lock = RLock()
        self.closed = False

    def _check(self):
        if self.down:
            raise BrokerUnavailableError("RabbitMQ is unavailable")

    def bind_queue(self, queue_name, routing_key):
        with self._lock:
            self._check()
            self._queues.setdefault(queue_name, deque())
            queues = self._bindings.setdefault(routing_key, [])
            if queue_name not in queues:
                queues.append(queue_name)

    def publish(self, routing_key, event):
        with self._lock:
            self._check()
            queues = self._bindings.get(routing_key, [])
            if not queues:
                raise BrokerUnavailableError("No queue is bound to this routing key")
            payload = json.loads(json.dumps(event))
            self.published.append((routing_key, payload))
            for queue_name in queues:
                self._queues[queue_name].append(json.loads(json.dumps(payload)))

    def consume_available(self, queue_name, handler, limit=50):
        with self._lock:
            self._check()
            queue = self._queues.setdefault(queue_name, deque())
            processed = 0
            while queue and processed < limit:
                handler(queue[0])
                queue.popleft()  # Odpowiednik ACK, dopiero po powodzeniu handlera.
                processed += 1
            return processed

    def close(self):
        self.closed = True


class RabbitEventBroker:
    """Trwaly topic exchange, persistent JSON, publisher confirms i reczny ACK."""

    def __init__(self, amqp_url=None, exchange_name=None):
        self.amqp_url = amqp_url or os.getenv("RABBITMQ_URL", "amqp://guest:guest@127.0.0.1:5672/%2F")
        self.exchange_name = exchange_name or os.getenv("RABBITMQ_EXCHANGE", "warehouse.events")

    @contextmanager
    def _channel(self):
        connection = None
        try:
            parameters = pika.URLParameters(self.amqp_url)
            parameters.socket_timeout = 2
            parameters.stack_timeout = 4
            parameters.blocked_connection_timeout = 2
            parameters.connection_attempts = 1
            connection = pika.BlockingConnection(parameters)
            channel = connection.channel()
            channel.exchange_declare(exchange=self.exchange_name, exchange_type="topic", durable=True)
            yield channel
        except (pika.exceptions.AMQPError, OSError) as exc:
            raise BrokerUnavailableError("RabbitMQ is unavailable") from exc
        finally:
            if connection is not None and connection.is_open:
                try:
                    connection.close()
                except (pika.exceptions.AMQPError, OSError):
                    pass

    def bind_queue(self, queue_name, routing_key):
        with self._channel() as channel:
            channel.queue_declare(queue=queue_name, durable=True)
            channel.queue_bind(exchange=self.exchange_name, queue=queue_name, routing_key=routing_key)

    def ping(self):
        with self._channel():
            return True

    def publish(self, routing_key, event):
        with self._channel() as channel:
            channel.confirm_delivery()
            channel.basic_publish(
                exchange=self.exchange_name, routing_key=routing_key,
                body=json.dumps(event).encode("utf-8"), mandatory=True,
                properties=pika.BasicProperties(content_type="application/json",
                                                delivery_mode=pika.DeliveryMode.Persistent,
                                                message_id=str(event["event_id"])),
            )

    def consume_available(self, queue_name, handler, limit=50):
        processed = 0
        with self._channel() as channel:
            channel.queue_declare(queue=queue_name, durable=True)
            while processed < limit:
                method, _, body = channel.basic_get(queue=queue_name, auto_ack=False)
                if method is None:
                    break
                try:
                    handler(json.loads(body.decode("utf-8")))
                except Exception:
                    channel.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
                    raise
                channel.basic_ack(delivery_tag=method.delivery_tag)
                processed += 1
        return processed

    def close(self):
        # Kazda operacja ma wlasne polaczenie zamykane przez _channel().
        pass
