from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select

from app.db_models import OrderDispatchRow, OrderRow, ProcessedEventRow
from app.messaging import BrokerUnavailableError, InMemoryEventBroker, RabbitEventBroker
from app.models.events import OUTBOUND_SHIPMENT_QUEUE, SHIPMENT_ROUTING_KEY, ShipmentHandedOver
from app.models.shipment import Shipment
from tests.test_auth_shipping import keys, new_shipment, world


def handed_over(world):
    clients, tokens, _, _ = world
    order, shipment = new_shipment(world)
    path = f"/shipments/{shipment['id']}"
    headers = tokens['SHIPPING']
    assert clients['shipping'].post(path + '/ready', headers=headers).status_code == 200
    result = clients['shipping'].post(path + '/hand-over', headers=headers)
    assert result.status_code == 200, result.text
    return order, result.json()


def test_event_waits_for_consume_and_invalidates_cache(world):
    clients, tokens, _, _ = world
    order, shipment = handed_over(world)
    broker = clients['shipping'].app.state.event_broker
    key, event = broker.published[0]
    assert key == SHIPMENT_ROUTING_KEY
    assert event['event_type'] == 'ShipmentHandedOver'
    assert event['shipment_id'] == shipment['id'] and event['order_id'] == order['id']
    assert set(event) == {'event_id', 'event_type', 'shipment_id', 'order_id', 'occurred_at'}
    UUID(event['event_id'])
    path = f"/orders/{order['id']}"
    headers = tokens['WAREHOUSE']
    clients['outbound'].get(path, headers=headers)
    before = clients['outbound'].get(path, headers=headers)
    assert before.json()['status'] == 'PACKED' and before.headers['x-cache'] == 'HIT'
    result = clients['outbound'].post('/orders/consume-events', headers=tokens['SHIPPING'])
    assert result.json() == {'consumed_messages': 1, 'unique_events_processed': 1}
    read = clients['outbound'].get(path, headers=headers)
    assert read.json()['status'] == 'SHIPPED' and read.headers['x-cache'] == 'MISS'
    assert clients['outbound'].get(path, headers=headers).headers['x-cache'] == 'HIT'


def test_duplicate_and_extra_fields_apply_once(world):
    clients, tokens, _, _ = world
    handed_over(world)
    broker = clients['shipping'].app.state.event_broker
    key, event = broker.published[0]
    broker.publish(key, {**event, 'future_field': {'ignored': True}})
    result = clients['outbound'].post('/orders/consume-events', headers=tokens['SHIPPING'])
    assert result.json() == {'consumed_messages': 2, 'unique_events_processed': 1}
    with clients['outbound'].app.state.order_repository.sessions() as db:
        assert db.scalar(select(func.count()).select_from(ProcessedEventRow)) == 1
        assert db.scalar(select(func.count()).select_from(OrderDispatchRow)) == 1


def test_invalid_operations_do_not_publish(world):
    clients, tokens, _, _ = world
    _, shipment = new_shipment(world)
    client, headers = clients['shipping'], tokens['SHIPPING']
    assert client.post(f"/shipments/{shipment['id']}/hand-over", headers=headers).status_code == 409
    assert client.post(f"/shipments/{uuid4()}/hand-over", headers=headers).status_code == 404
    assert client.post('/shipments', headers=headers, json={
        'order_id': shipment['order_id'], 'carrier': 'Test', 'weight_kg': -1}).status_code == 422
    assert client.app.state.event_broker.published == []


def test_broker_outage_keeps_pending_fact_and_sync_recovers(world):
    clients, tokens, _, _ = world
    order, shipment = new_shipment(world)
    client, headers = clients['shipping'], tokens['SHIPPING']
    path = f"/shipments/{shipment['id']}"
    assert client.post(path + '/ready', headers=headers).status_code == 200
    broker = client.app.state.event_broker
    broker.down = True
    assert client.post(path + '/hand-over', headers=headers).status_code == 503
    pending = client.get(path, headers=headers).json()
    assert pending['status'] == 'HANDED_OVER' and pending['notification_pending'] is True
    expected = ShipmentHandedOver.from_shipment(Shipment.model_validate(pending))
    assert clients['outbound'].post('/orders/consume-events', headers=headers).status_code == 503
    assert all(client.get('/health').status_code == 200 for client in clients.values())
    assert client.post(path + '/hand-over', headers=headers).status_code == 409
    broker.down = False
    assert client.post(path + '/sync', headers=headers).json()['notification_pending'] is False
    assert broker.published[0][1]['event_id'] == str(expected.event_id)
    assert client.post(path + '/sync', headers=headers).status_code == 200
    assert len(broker.published) == 1
    assert clients['outbound'].post('/orders/consume-events', headers=headers).json()['unique_events_processed'] == 1
    assert clients['outbound'].get(f"/orders/{order['id']}", headers=tokens['WAREHOUSE']).json()['status'] == 'SHIPPED'


def test_consume_requires_shipping_role(world):
    clients, tokens, _, _ = world
    client = clients['outbound']
    assert client.post('/orders/consume-events').status_code == 401
    assert client.post('/orders/consume-events', headers=tokens['WAREHOUSE']).status_code == 403
    assert client.post('/orders/consume-events', headers=tokens['SHIPPING']).json() == {
        'consumed_messages': 0, 'unique_events_processed': 0}
    assert client.post('/orders/consume-events?limit=0', headers=tokens['SHIPPING']).status_code == 422


def test_cache_failure_requeues_and_duplicate_repairs_invalidation(world):
    clients, tokens, _, _ = world
    order, _ = handed_over(world)
    client = clients['outbound']
    headers = tokens['SHIPPING']
    path = f"/orders/{order['id']}"
    client.get(path, headers=tokens['WAREHOUSE'])
    client.app.state.redis.fail.add('delete')
    assert client.post('/orders/consume-events', headers=headers).status_code == 503
    assert client.app.state.order_repository.get(UUID(order['id'])).status == 'SHIPPED'
    client.app.state.redis.fail.clear()
    assert client.post('/orders/consume-events', headers=headers).json() == {
        'consumed_messages': 1, 'unique_events_processed': 0}
    assert client.get(path, headers=tokens['WAREHOUSE']).headers['x-cache'] == 'MISS'


def test_invalid_message_stays_queued(world):
    clients, tokens, _, _ = world
    broker = clients['outbound'].app.state.event_broker
    broker.publish(SHIPMENT_ROUTING_KEY, {'event_type': 'ShipmentHandedOver'})
    for _ in range(2):
        result = clients['outbound'].post('/orders/consume-events', headers=tokens['SHIPPING'])
        assert result.status_code == 422
    assert len(broker._queues[OUTBOUND_SHIPMENT_QUEUE]) == 1


def test_deduplication_survives_consumer_restart(app_factory):
    broker = InMemoryEventBroker()
    first_app = app_factory(domain='outbound', event_broker=broker)
    with TestClient(first_app) as client:
        order = client.post('/orders', json={'recipient_name': 'Test', 'recipient_phone': '1',
            'recipient_address': 'Test', 'items': [{'sku': 'SKU-001', 'quantity': 1}]}).json()
        with first_app.state.order_repository.sessions.begin() as db:
            db.get(OrderRow, UUID(order['id'])).status = 'PACKED'
        event = ShipmentHandedOver(event_id=uuid4(), event_type='ShipmentHandedOver',
                                   order_id=order['id'], shipment_id=uuid4(), occurred_at=datetime.now(timezone.utc))
        broker.publish(SHIPMENT_ROUTING_KEY, event.model_dump(mode='json'))
        assert client.post('/orders/consume-events').json()['unique_events_processed'] == 1
    broker.publish(SHIPMENT_ROUTING_KEY, event.model_dump(mode='json'))
    with TestClient(app_factory(domain='outbound', event_broker=broker)) as client:
        assert client.post('/orders/consume-events').json() == {'consumed_messages': 1, 'unique_events_processed': 0}
        assert client.get(f"/orders/{order['id']}").json()['status'] == 'SHIPPED'


def test_outbound_http_starts_when_broker_is_down(app_factory):
    broker = InMemoryEventBroker(down=True)
    with TestClient(app_factory(domain='outbound', event_broker=broker)) as client:
        assert client.get('/health').status_code == 200
        assert client.get('/orders').status_code == 200
        assert client.post('/orders/consume-events').status_code == 503
        broker.down = False
        assert client.post('/orders/consume-events').json()['consumed_messages'] == 0


@pytest.mark.parametrize('failed', [False, True])
def test_rabbit_ack_is_sent_only_after_successful_handler(monkeypatch, failed):
    events = []

    class Channel:
        received = False

        def queue_declare(self, **kwargs):
            pass

        def basic_get(self, **kwargs):
            assert kwargs['auto_ack'] is False
            if self.received:
                return None, None, None
            self.received = True
            return SimpleNamespace(delivery_tag=1), None, b'{}'

        def basic_ack(self, **kwargs):
            events.append('ack')

        def basic_nack(self, **kwargs):
            assert kwargs['requeue'] is True
            events.append('nack')

    @contextmanager
    def channel():
        yield Channel()

    def handler(_):
        events.append('handler')
        if failed:
            raise RuntimeError('Failed database write')

    broker = RabbitEventBroker()
    monkeypatch.setattr(broker, '_channel', channel)
    if failed:
        with pytest.raises(RuntimeError):
            broker.consume_available('test', handler)
        assert events == ['handler', 'nack']
    else:
        assert broker.consume_available('test', handler) == 1
        assert events == ['handler', 'ack']
