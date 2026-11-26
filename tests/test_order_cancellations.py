from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from threading import Barrier
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from app.db_models import OutboxEventRow, ProcessedEventRow, ReservationBatchRow, StockRow
from app.messaging import BrokerUnavailableError, InMemoryEventBroker
from app.models.events import CANCELLATION_ROUTING_KEY, INVENTORY_CANCELLATION_QUEUE, cancellation_event_id
from tests.test_auth_shipping import keys, world
from tests.test_inventory_shipments import post_supply, reserved, shipment_event
from tests.test_reservation_events import order, reserve


CONSUME = '/reservations/consume-cancellation-events'


def stock(client):
    return client.get('/stocks').json()[0]


def cancel(client, order_id):
    response = client.post('/orders/' + order_id + '/cancel')
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'CANCELLED'
    return response


def test_cancellation_is_manual_and_keeps_physical_quantity(client):
    order_id = reserved(client)
    path = '/stocks/' + stock(client)['id']
    client.get(path)
    assert client.get(path).headers['x-cache'] == 'HIT'
    cancel(client, order_id)
    assert sum(r['quantity'] for r in stock(client)['reservations']) == 3
    assert client.post('/orders/' + order_id + '/pack').status_code == 409
    assert client.post('/orders/' + order_id + '/reserve').status_code == 409
    assert client.post(CONSUME).json() == {'consumed_messages': 1, 'unique_events_processed': 1}
    after = client.get(path)
    assert after.headers['x-cache'] == 'MISS'
    assert after.json()['quantity'] == 10 and after.json()['reservations'] == []
    with client.app.state.allocation_service.sessions() as db:
        batch = db.get(ReservationBatchRow, UUID(order_id))
        assert batch.status == 'RELEASED' and batch.shipment_id is None
        assert batch.released_stock_ids == [stock(client)['id']]
    cancel(client, order_id)
    assert client.post('/orders/' + order_id + '/sync-cancellation').status_code == 200
    assert client.post(CONSUME).json()['consumed_messages'] == 0
    assert sum(key == CANCELLATION_ROUTING_KEY for key, _ in client.app.state.event_broker.published) == 1


def test_release_from_multiple_locations_preserves_other_orders(client):
    client.patch('/stocks/' + stock(client)['id'], json={'quantity': 2})
    post_supply(client, 'SKU-001', 'SECOND', 2)
    cancelled = reserved(client, 3)
    other = reserved(client, 1)
    cancel(client, cancelled)
    assert client.post(CONSUME).status_code == 200
    rows = client.get('/stocks').json()
    assert sum(row['quantity'] for row in rows) == 4
    assert [r for row in rows for r in row['reservations']] == [{'order_id': other, 'quantity': 1}]
    assert sum(row['quantity'] - sum(r['quantity'] for r in row['reservations']) for row in rows) == 3


@pytest.mark.parametrize('status', ['NEW', 'RESERVATION_PENDING', 'RESERVATION_REJECTED', 'PACKED', 'SHIPPED'])
def test_only_reserved_order_can_be_cancelled(client, status):
    path = order(client, 11 if status == 'RESERVATION_REJECTED' else 3)
    if status != 'NEW':
        reserve(client, path)
    if status in {'RESERVATION_REJECTED', 'PACKED', 'SHIPPED'}:
        assert client.post('/reservations/consume-events').status_code == 200
        assert client.post('/orders/consume-reservation-events').status_code == 200
    if status in {'PACKED', 'SHIPPED'}:
        assert client.post(path + '/pack').status_code == 200
    if status == 'SHIPPED':
        assert client.post(path + '/dispatch', json={'shipment_id': str(uuid4())}).status_code == 200
    assert client.get(path).json()['status'] == status
    assert client.post(path + '/cancel').status_code == 409
    assert client.get(path).json()['status'] == status
    assert not any(key == CANCELLATION_ROUTING_KEY for key, _ in client.app.state.event_broker.published)


def test_broker_outage_and_manual_retry_keep_saved_cancellation(client):
    order_id = reserved(client)
    path = '/orders/' + order_id
    broker = client.app.state.event_broker
    broker.down = True
    assert client.post(path + '/cancel').status_code == 503
    assert client.get(path).json()['status'] == 'CANCELLED'
    assert stock(client)['quantity'] == 10 and len(stock(client)['reservations']) == 1
    with client.app.state.order_repository.sessions() as db:
        assert db.get(OutboxEventRow, cancellation_event_id(order_id)).published is False
    broker.down = False
    assert client.post(path + '/sync-cancellation').status_code == 200
    assert client.post(CONSUME).json()['unique_events_processed'] == 1
    assert stock(client)['reservations'] == []


def test_lost_publish_confirmation_and_restart_release_only_once(app_factory):
    broker = InMemoryEventBroker()
    with TestClient(app_factory(event_broker=broker)) as first:
        order_id = reserved(first)
        original = broker.publish

        def lost(key, payload):
            original(key, payload)
            raise BrokerUnavailableError('lost confirmation')

        broker.publish = lost
        assert first.post('/orders/' + order_id + '/cancel').status_code == 503
        broker.publish = original
        assert first.post(CONSUME).json()['unique_events_processed'] == 1
    with TestClient(app_factory(event_broker=broker)) as second:
        assert second.post('/orders/' + order_id + '/sync-cancellation').status_code == 200
        assert second.post(CONSUME).json() == {'consumed_messages': 1, 'unique_events_processed': 0}
        assert stock(second)['quantity'] == 10 and stock(second)['reservations'] == []


def test_cache_failure_is_repaired_after_committed_release(client):
    order_id = reserved(client)
    path = '/stocks/' + stock(client)['id']
    client.get(path)
    cancel(client, order_id)
    client.app.state.redis.fail.add('delete')
    assert client.post(CONSUME).status_code == 503
    with client.app.state.allocation_service.sessions() as db:
        assert db.get(ReservationBatchRow, UUID(order_id)).status == 'RELEASED'
        assert db.scalar(select(StockRow)).reservations == []
    assert len(client.app.state.event_broker._queues[INVENTORY_CANCELLATION_QUEUE]) == 1
    client.app.state.redis.fail.clear()
    assert client.post(CONSUME).json() == {'consumed_messages': 1, 'unique_events_processed': 0}
    read = client.get(path)
    assert read.headers['x-cache'] == 'MISS' and read.json()['reservations'] == []


def test_database_failure_rolls_back_release_and_event(client, monkeypatch):
    order_id = reserved(client)
    cancel(client, order_id)
    allocation = client.app.state.allocation_service
    original = allocation.release_in_transaction

    def fail(db, event):
        original(db, event)
        db.flush()
        raise OperationalError('disk failure', {}, Exception('disk failure'))

    monkeypatch.setattr(allocation, 'release_in_transaction', fail)
    assert client.post(CONSUME).status_code == 503
    assert len(stock(client)['reservations']) == 1
    with allocation.sessions() as db:
        assert db.get(ReservationBatchRow, UUID(order_id)).status == 'RESERVED'
        assert db.scalar(select(func.count()).select_from(ProcessedEventRow).where(
            ProcessedEventRow.event_type == 'OrderCancelled')) == 0
    monkeypatch.setattr(allocation, 'release_in_transaction', original)
    assert client.post(CONSUME).json()['unique_events_processed'] == 1


def test_released_batch_cannot_be_reserved_or_consumed(client):
    order_id = reserved(client)
    cancel(client, order_id)
    client.post(CONSUME)
    assert client.post('/reservations', json={'order_id': order_id,
        'items': [{'sku': 'SKU-001', 'quantity': 3}]}).status_code == 409
    assert client.post('/stocks/' + stock(client)['id'] + '/reservations', json={
        'order_id': order_id, 'quantity': 1}).status_code == 409
    client.app.state.event_broker.publish('shipment.handed_over', shipment_event(order_id))
    assert client.post('/reservations/consume-shipment-events').status_code == 409
    assert stock(client)['quantity'] == 10


def test_changed_payload_under_same_event_id_is_rejected(client):
    order_id = reserved(client)
    cancel(client, order_id)
    broker = client.app.state.event_broker
    payload = broker.published[-1][1]
    assert client.post(CONSUME).status_code == 200
    broker.publish(CANCELLATION_ROUTING_KEY, {**payload, 'items': [{'sku': 'SKU-001', 'quantity': 4}]})
    assert client.post(CONSUME).status_code == 409
    assert stock(client)['quantity'] == 10


def test_consumed_reservation_cannot_be_released(client):
    order_id = reserved(client)
    client.app.state.event_broker.publish('shipment.handed_over', shipment_event(order_id))
    assert client.post('/reservations/consume-shipment-events').status_code == 200
    cancel(client, order_id)
    assert client.post(CONSUME).status_code == 409
    assert stock(client)['quantity'] == 7
    with client.app.state.allocation_service.sessions() as db:
        assert db.get(ReservationBatchRow, UUID(order_id)).status == 'CONSUMED'


def test_incomplete_reservations_are_not_partially_released(client):
    order_id = reserved(client)
    cancel(client, order_id)
    with client.app.state.allocation_service.sessions.begin() as db:
        row = db.scalar(select(StockRow))
        row.reservations = [{'order_id': order_id, 'quantity': 2}]
    before = stock(client)
    assert client.post(CONSUME).status_code == 409
    assert stock(client) == before
    with client.app.state.allocation_service.sessions() as db:
        assert db.get(ReservationBatchRow, UUID(order_id)).status == 'RESERVED'


def test_pack_and_cancel_cannot_both_succeed(app_factory):
    broker = InMemoryEventBroker()
    with ExitStack() as stack:
        clients = [stack.enter_context(TestClient(app_factory(event_broker=broker))) for _ in range(2)]
        order_id = reserved(clients[0])
        barrier = Barrier(2)

        def action(client, name):
            barrier.wait(timeout=5)
            return client.post('/orders/' + order_id + '/' + name).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [pool.submit(action, client, name) for client, name in zip(clients, ['pack', 'cancel'])]
            assert sorted(job.result(timeout=10) for job in jobs) == [200, 409]


def test_cancellation_endpoints_require_warehouse_role(world):
    clients, tokens, _, _ = world
    for client, path in [(clients['inventory'], CONSUME),
                         (clients['outbound'], '/orders/' + str(uuid4()) + '/cancel'),
                         (clients['outbound'], '/orders/' + str(uuid4()) + '/sync-cancellation')]:
        assert client.post(path).status_code == 401
        assert client.post(path, headers=tokens['SHIPPING']).status_code == 403
        assert client.post(path, headers=tokens['WAREHOUSE']).status_code in {200, 404}
    assert clients['inventory'].post(CONSUME + '?limit=0', headers=tokens['ADMIN']).status_code == 422
    assert clients['inventory'].post(CONSUME + '?limit=501', headers=tokens['ADMIN']).status_code == 422
