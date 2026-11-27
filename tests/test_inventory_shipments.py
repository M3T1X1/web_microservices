from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import datetime, timezone
from threading import Barrier
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from app.db_models import ProcessedEventRow, ReservationBatchRow, StockRow
from app.messaging import InMemoryEventBroker
from app.models.events import INVENTORY_SHIPMENT_QUEUE, SHIPMENT_ROUTING_KEY, ShipmentHandedOver
from tests.test_auth_shipping import keys, world
from tests.test_messaging import handed_over
from tests.test_reservation_events import order, reserve


CONSUME = '/reservations/consume-shipment-events'


def shipment_event(order_id, shipment_id=None):
    return ShipmentHandedOver(event_id=uuid4(), event_type='ShipmentHandedOver', order_id=order_id,
                             shipment_id=shipment_id or uuid4(), occurred_at=datetime.now(timezone.utc)).model_dump(mode='json')


def reserved(client, quantity=3):
    path = order(client, quantity)
    reserve(client, path)
    assert client.post('/reservations/consume-events').status_code == 200
    assert client.post('/orders/consume-reservation-events').status_code == 200
    return path.rsplit('/', 1)[-1]


def post_supply(client, sku, location, quantity):
    response = client.post('/receipts', json={'supplier': 'Test', 'items': [
        {'sku': sku, 'target_location': location, 'quantity_received': quantity}]})
    assert response.status_code == 201, response.text
    path = '/receipts/' + response.json()['id']
    assert client.post(path + '/accept').status_code == 200
    assert client.post(path + '/post').status_code == 200


@pytest.mark.parametrize('inventory_first', [False, True])
def test_independent_queues_and_manual_settlement(world, inventory_first):
    clients, tokens, _, _ = world
    order_data, shipment = handed_over(world)
    inventory, outbound = clients['inventory'], clients['outbound']
    headers = tokens['WAREHOUSE']
    stock = inventory.get('/stocks', headers=headers).json()[0]
    path = '/stocks/' + stock['id']
    assert stock['quantity'] == 10 and sum(r['quantity'] for r in stock['reservations']) == 3
    inventory.get(path, headers=headers)
    assert inventory.get(path, headers=headers).headers['x-cache'] == 'HIT'

    def consume_inventory():
        result = inventory.post(CONSUME, headers=headers)
        assert result.json() == {'consumed_messages': 1, 'unique_events_processed': 1}
        current = inventory.get(path, headers=headers)
        assert current.headers['x-cache'] == 'MISS'
        assert current.json()['quantity'] == 7 and current.json()['reservations'] == []
        with inventory.app.state.allocation_service.sessions() as db:
            batch = db.get(ReservationBatchRow, UUID(order_data['id']))
            assert batch.status == 'CONSUMED' and str(batch.shipment_id) == shipment['id']
            assert batch.consumed_stock_ids == [stock['id']]

    def consume_outbound():
        assert outbound.post('/orders/consume-events', headers=tokens['SHIPPING']).json() == {
            'consumed_messages': 1, 'unique_events_processed': 1}

    if inventory_first:
        consume_inventory()
        assert outbound.get('/orders/' + order_data['id'], headers=headers).json()['status'] == 'PACKED'
        consume_outbound()
    else:
        consume_outbound()
        assert inventory.get(path, headers=headers).json()['quantity'] == 10
        consume_inventory()
    assert outbound.get('/orders/' + order_data['id'], headers=headers).json()['status'] == 'SHIPPED'
    assert inventory.post(CONSUME, headers=headers).json()['consumed_messages'] == 0


def test_settlement_from_multiple_locations_preserves_other_order(client):
    first = client.get('/stocks').json()[0]
    client.patch('/stocks/' + first['id'], json={'quantity': 2})
    post_supply(client, 'SKU-001', 'SECOND', 2)
    shipped_order = reserved(client, 3)
    other_order = reserved(client, 1)
    before = client.get('/stocks').json()
    assert len([row for row in before if any(r['order_id'] == shipped_order for r in row['reservations'])]) == 2
    broker = client.app.state.event_broker
    broker.publish(SHIPMENT_ROUTING_KEY, shipment_event(shipped_order))
    assert client.post(CONSUME).status_code == 200
    after = client.get('/stocks').json()
    assert sum(row['quantity'] for row in after) == 1
    assert [r for row in after for r in row['reservations']] == [{'order_id': other_order, 'quantity': 1}]
    assert sum(row['quantity'] - sum(r['quantity'] for r in row['reservations']) for row in after) == 0


def test_duplicate_and_restart_do_not_subtract_twice(app_factory):
    broker = InMemoryEventBroker()
    with TestClient(app_factory(event_broker=broker)) as first:
        order_id = reserved(first)
        event = shipment_event(order_id)
        broker.publish(SHIPMENT_ROUTING_KEY, event)
        assert first.post(CONSUME).json()['unique_events_processed'] == 1
    broker.publish(SHIPMENT_ROUTING_KEY, {**event, 'future_field': 'ignored'})
    with TestClient(app_factory(event_broker=broker)) as second:
        assert second.post(CONSUME).json() == {'consumed_messages': 1, 'unique_events_processed': 0}
        assert second.get('/stocks').json()[0]['quantity'] == 7
        with second.app.state.allocation_service.sessions() as db:
            assert db.scalar(select(func.count()).select_from(ProcessedEventRow).where(
                ProcessedEventRow.event_type == 'ShipmentHandedOver')) == 1


def test_cache_failure_after_commit_is_repaired_by_duplicate(client):
    order_id = reserved(client)
    stock = client.get('/stocks').json()[0]
    path = '/stocks/' + stock['id']
    client.get(path)
    assert client.get(path).headers['x-cache'] == 'HIT'
    broker = client.app.state.event_broker
    broker.publish(SHIPMENT_ROUTING_KEY, shipment_event(order_id))
    client.app.state.redis.fail.add('delete')
    assert client.post(CONSUME).status_code == 503
    with client.app.state.allocation_service.sessions() as db:
        assert db.get(StockRow, UUID(stock['id'])).quantity == 7
        assert db.get(ReservationBatchRow, UUID(order_id)).status == 'CONSUMED'
    assert len(broker._queues[INVENTORY_SHIPMENT_QUEUE]) == 1
    client.app.state.redis.fail.clear()
    assert client.post(CONSUME).json() == {'consumed_messages': 1, 'unique_events_processed': 0}
    response = client.get(path)
    assert response.headers['x-cache'] == 'MISS' and response.json()['quantity'] == 7


def test_database_failure_rolls_back_entire_settlement(client, monkeypatch):
    order_id = reserved(client)
    broker = client.app.state.event_broker
    broker.publish(SHIPMENT_ROUTING_KEY, shipment_event(order_id))
    allocation = client.app.state.allocation_service
    original = allocation.consume_in_transaction

    def fail(db, event):
        original(db, event)
        db.flush()
        raise OperationalError('simulated disk failure', {}, Exception('disk failure'))

    monkeypatch.setattr(allocation, 'consume_in_transaction', fail)
    assert client.post(CONSUME).status_code == 503
    assert client.get('/stocks').json()[0]['quantity'] == 10
    with allocation.sessions() as db:
        assert db.get(ReservationBatchRow, UUID(order_id)).status == 'RESERVED'
        assert db.scalar(select(func.count()).select_from(ProcessedEventRow).where(
            ProcessedEventRow.event_type == 'ShipmentHandedOver')) == 0
    monkeypatch.setattr(allocation, 'consume_in_transaction', original)
    assert client.post(CONSUME).json()['unique_events_processed'] == 1


@pytest.mark.parametrize('problem', ['missing_batch', 'incomplete_reservations', 'insufficient_physical_stock'])
def test_inconsistent_reservation_is_requeued_without_changes(client, problem):
    order_id = reserved(client)
    sessions = client.app.state.allocation_service.sessions
    with sessions.begin() as db:
        row = db.scalar(select(StockRow))
        if problem == 'missing_batch':
            db.delete(db.get(ReservationBatchRow, UUID(order_id)))
        elif problem == 'incomplete_reservations':
            row.reservations = []
        else:
            row.quantity = 1
    before = client.get('/stocks').json()
    broker = client.app.state.event_broker
    broker.publish(SHIPMENT_ROUTING_KEY, shipment_event(order_id))
    assert client.post(CONSUME).status_code == 409
    assert client.get('/stocks').json() == before
    assert len(broker._queues[INVENTORY_SHIPMENT_QUEUE]) == 1


def test_consumed_order_cannot_reserve_again_or_use_another_shipment(client):
    order_id = reserved(client)
    broker = client.app.state.event_broker
    event = shipment_event(order_id)
    broker.publish(SHIPMENT_ROUTING_KEY, event)
    assert client.post(CONSUME).status_code == 200
    stock = client.get('/stocks').json()[0]
    assert client.post('/reservations', json={'order_id': order_id,
        'items': [{'sku': 'SKU-001', 'quantity': 3}]}).status_code == 409
    assert client.post('/stocks/' + stock['id'] + '/reservations', json={
        'order_id': order_id, 'quantity': 1}).status_code == 409
    broker.publish(SHIPMENT_ROUTING_KEY, shipment_event(order_id))
    assert client.post(CONSUME).status_code == 409
    assert client.get('/stocks').json()[0]['quantity'] == 7


def test_reusing_event_id_with_different_payload_is_rejected(client):
    order_id = reserved(client)
    event = shipment_event(order_id)
    broker = client.app.state.event_broker
    broker.publish(SHIPMENT_ROUTING_KEY, event)
    assert client.post(CONSUME).status_code == 200
    broker.publish(SHIPMENT_ROUTING_KEY, {**event, 'shipment_id': str(uuid4())})
    assert client.post(CONSUME).status_code == 409
    assert client.get('/stocks').json()[0]['quantity'] == 7


def test_parallel_duplicate_delivery_subtracts_once(app_factory):
    broker = InMemoryEventBroker()
    with ExitStack() as stack:
        clients = [stack.enter_context(TestClient(app_factory(event_broker=broker))) for _ in range(2)]
        event = shipment_event(reserved(clients[0]))
        barrier = Barrier(2)

        def handle(client):
            barrier.wait(timeout=5)
            return client.app.state.inventory_shipping_service.handle(event)

        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [pool.submit(handle, client) for client in clients]
            assert sorted(job.result(timeout=10) for job in jobs) == [False, True]
        assert clients[0].get('/stocks').json()[0]['quantity'] == 7


def test_broker_outage_does_not_change_stock(client):
    reserved(client)
    client.app.state.event_broker.down = True
    assert client.post(CONSUME).status_code == 503
    assert client.get('/stocks').json()[0]['quantity'] == 10
    client.app.state.event_broker.down = False
    assert client.post(CONSUME).json()['consumed_messages'] == 0
