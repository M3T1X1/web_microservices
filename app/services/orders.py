from uuid import UUID
from fastapi import HTTPException
from pydantic import ValidationError

from app.cache import JsonCache
from app.models.order import Order, OrderCreate
from app.models.events import (OUTBOUND_SHIPMENT_QUEUE, SHIPMENT_ROUTING_KEY, ShipmentHandedOver,
                              OUTBOUND_RESERVATION_QUEUE, RESERVATION_RESERVED_KEY, RESERVATION_REJECTED_KEY,
                              reservation_request_id, reservation_result)
from app.messaging import BrokerUnavailableError, RabbitEventBroker
from app.outbox import publish_event
from app.repositories import OrderRepository
from app.services.cached_read import cached_read


class OrderService:
    def __init__(self, repository: OrderRepository, cache: JsonCache, event_broker=None):
        self.repository, self.cache = repository, cache
        self.event_broker = event_broker if event_broker is not None else RabbitEventBroker()

    def consume_events(self, limit=50):
        unique = 0

        def handle_event(payload):
            nonlocal unique
            event = ShipmentHandedOver.model_validate(payload)
            applied = self.repository.apply_shipment_event(event)
            # Takze duplikat naprawia cache, jesli poprzednia proba padla po COMMIT.
            if not self.cache.delete(event.order_id):
                raise HTTPException(503, "Cache invalidation failed; event remains queued")
            unique += int(applied)

        try:
            self.event_broker.bind_queue(OUTBOUND_SHIPMENT_QUEUE, SHIPMENT_ROUTING_KEY)
            consumed = self.event_broker.consume_available(OUTBOUND_SHIPMENT_QUEUE, handle_event, limit=limit)
        except BrokerUnavailableError as exc:
            raise HTTPException(503, "RabbitMQ broker is unavailable") from exc
        except (ValidationError, ValueError, TypeError) as exc:
            raise HTTPException(422, "Invalid shipment event; message remains queued") from exc
        return {"consumed_messages": consumed, "unique_events_processed": unique}

    def create(self, body: OrderCreate) -> Order:
        return self.repository.create(body)

    def list(self) -> list[Order]:
        return self.repository.list()

    def get(self, order_id: UUID) -> tuple[Order, str]:
        return cached_read(order_id, self.repository, self.cache, Order)

    def pack(self, order_id: UUID) -> Order:
        result = self.repository.pack(order_id)
        self.cache.delete(order_id)
        return result

    def reserve(self, order_id):
        result = self.repository.start_reservation(order_id)
        self.cache.delete(order_id)
        if result.reservation_notification_pending:
            return self.sync_reservation(order_id)
        return result

    def sync_reservation(self, order_id):
        order = self.repository.get(order_id)
        if order.reservation_attempt_id is None:
            raise HTTPException(409, "Order has no reservation request to synchronize")
        try:
            publish_event(self.repository.sessions, self.event_broker,
                          reservation_request_id(order.reservation_attempt_id))
        except BrokerUnavailableError as exc:
            raise HTTPException(503, "RabbitMQ unavailable; request saved, retry sync-reservation") from exc
        result = self.repository.confirm_reservation_publication(order_id, order.reservation_attempt_id)
        self.cache.delete(order_id)
        return result

    def consume_reservation_events(self, limit=50):
        unique = 0

        def handle(payload):
            nonlocal unique
            event = reservation_result(payload)
            applied = self.repository.apply_reservation_result(event)
            if not self.cache.delete(event.order_id):
                raise HTTPException(503, "Cache invalidation failed; result remains queued")
            unique += int(applied)

        try:
            for key in (RESERVATION_RESERVED_KEY, RESERVATION_REJECTED_KEY):
                self.event_broker.bind_queue(OUTBOUND_RESERVATION_QUEUE, key)
            consumed = self.event_broker.consume_available(OUTBOUND_RESERVATION_QUEUE, handle, limit=limit)
        except BrokerUnavailableError as exc:
            raise HTTPException(503, "RabbitMQ broker is unavailable") from exc
        except (ValidationError, ValueError, TypeError) as exc:
            raise HTTPException(422, "Invalid reservation result; message remains queued") from exc
        return {"consumed_messages": consumed, "unique_events_processed": unique}

    def dispatch(self, order_id, shipment_id):
        result = self.repository.dispatch(order_id, shipment_id)
        self.cache.delete(order_id)
        return result
