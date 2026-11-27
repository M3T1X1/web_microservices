from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import datetime, timezone
from threading import Barrier
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from app.db_models import OutboxEventRow, ProcessedEventRow, ReservationBatchRow
from app.messaging import BrokerUnavailableError, InMemoryEventBroker
from app.models.events import (RESERVATION_REQUEST_KEY, RESERVATION_RESERVED_KEY, RESERVATION_REJECTED_KEY,
                              OrderStockReserved, reservation_result_id)


REQUESTS = '/reservations/consume-events'
RESULTS = '/orders/consume-reservation-events'


def order(client, quantity=4, items=None):
    response = client.post('/orders', json={'recipient_name': 'Test', 'recipient_phone': '1',
        'recipient_address': 'Test', 'items': items or [{'sku': 'SKU-001', 'quantity': quantity}]})
    assert response.status_code == 201, response.text
    return '/orders/' + response.json()['id']


def stock(client):
    return client.get('/stocks').json()[0]


def reserve(client, path):
    response = client.post(path + '/reserve')
    assert response.status_code == 202, response.text
    return response.json()


def test_three_manual_steps_pack_guard_and_no_http(client):
    path = order(client)
    first = reserve(client, path)
    assert first['status'] == 'RESERVATION_PENDING'
    assert stock(client)['reservations'] == []
    assert client.post(path + '/pack').status_code == 409
    assert reserve(client, path)['reservation_attempt_id'] == first['reservation_attempt_id']
    assert client.post(REQUESTS).json() == {'consumed_messages': 1, 'unique_events_processed': 1}
    assert stock(client)['reservations'][0]['quantity'] == 4
    assert client.get(path).json()['status'] == 'RESERVATION_PENDING'
    assert client.post(path + '/pack').status_code == 409
    assert client.post(RESULTS).json() == {'consumed_messages': 1, 'unique_events_processed': 1}
    assert client.get(path).json()['status'] == 'RESERVED'
    assert reserve(client, path)['status'] == 'RESERVED'
    assert client.post(REQUESTS).json()['consumed_messages'] == 0
    assert client.post(path + '/pack').json()['status'] == 'PACKED'


def test_rejection_is_consumed_and_deliberate_retry_has_new_id(client):
    path = order(client, 11)
    first = reserve(client, path)
    broker = client.app.state.event_broker
    old_request = broker.published[-1][1]
    assert client.post(REQUESTS).status_code == 200
    old_result = broker.published[-1][1]
    assert old_result['event_type'] == 'OrderStockReservationRejected'
    assert client.post(REQUESTS).json()['consumed_messages'] == 0
    assert stock(client)['reservations'] == []
    assert client.post(RESULTS).status_code == 200
    rejected = client.get(path).json()
    assert rejected['status'] == 'RESERVATION_REJECTED' and rejected['reservation_error']
    assert client.post(path + '/pack').status_code == 409
    assert client.patch('/stocks/' + stock(client)['id'], json={'quantity': 12}).status_code == 200
    second = reserve(client, path)
    assert second['reservation_attempt_id'] != first['reservation_attempt_id']
    broker.publish(RESERVATION_REQUEST_KEY, old_request)
    assert client.post(REQUESTS).json() == {'consumed_messages': 2, 'unique_events_processed': 1}
    broker.publish(RESERVATION_REJECTED_KEY, old_result)
    assert client.post(RESULTS).json() == {'consumed_messages': 2, 'unique_events_processed': 1}
    assert client.get(path).json()['status'] == 'RESERVED'
    assert sum(r['quantity'] for r in stock(client)['reservations']) == 11


def test_late_result_cannot_overwrite_current_attempt(client):
    path = order(client)
    current = reserve(client, path)
    previous_attempt = uuid4()
    old = OrderStockReserved(event_id=reservation_result_id(previous_attempt), event_type='OrderStockReserved',
        attempt_id=previous_attempt, order_id=path.rsplit('/', 1)[1], items=[{'sku': 'SKU-001', 'quantity': 4}],
        occurred_at=datetime.now(timezone.utc))
    client.app.state.event_broker.publish(RESERVATION_RESERVED_KEY, old.model_dump(mode='json'))
    assert client.post(RESULTS).json()['unique_events_processed'] == 0
    assert client.get(path).json()['status'] == 'RESERVATION_PENDING'
    assert client.get(path).json()['reservation_attempt_id'] == current['reservation_attempt_id']


def test_request_publication_outage_can_be_retried(client):
    broker = client.app.state.event_broker
    broker.down = True
    path = order(client)
    assert client.post(path + '/reserve').status_code == 503
    saved = client.get(path).json()
    assert saved['status'] == 'RESERVATION_PENDING' and saved['reservation_notification_pending']
    with client.app.state.order_repository.sessions() as db:
        outgoing = db.scalar(select(OutboxEventRow))
        assert outgoing.published is False
        assert outgoing.payload['attempt_id'] == saved['reservation_attempt_id']
    broker.down = False
    synced = client.post(path + '/sync-reservation')
    assert synced.status_code == 200
    assert synced.json()['reservation_attempt_id'] == saved['reservation_attempt_id']
    assert synced.json()['reservation_notification_pending'] is False
    assert client.post(REQUESTS).json()['consumed_messages'] == 1
    assert client.post(RESULTS).json()['consumed_messages'] == 1
    assert client.get(path).json()['status'] == 'RESERVED'


def test_lost_result_confirmation_duplicates_messages_not_reservations(client, monkeypatch):
    path = order(client)
    reserve(client, path)
    broker = client.app.state.event_broker
    original = broker.publish
    lost = [False]

    def publish(key, payload):
        original(key, payload)
        if key == RESERVATION_RESERVED_KEY and not lost[0]:
            lost[0] = True
            raise BrokerUnavailableError('Lost confirmation')

    monkeypatch.setattr(broker, 'publish', publish)
    assert client.post(REQUESTS).status_code == 503
    assert sum(r['quantity'] for r in stock(client)['reservations']) == 4
    assert client.post(REQUESTS).json() == {'consumed_messages': 1, 'unique_events_processed': 0}
    assert client.post(RESULTS).json() == {'consumed_messages': 2, 'unique_events_processed': 1}
    assert len(stock(client)['reservations']) == 1


def test_result_can_be_published_manually_after_outage(client, monkeypatch):
    path = order(client)
    reserve(client, path)
    broker = client.app.state.event_broker
    original = broker.publish
    monkeypatch.setattr(broker, 'publish', lambda *_: (_ for _ in ()).throw(BrokerUnavailableError('down')))
    assert client.post(REQUESTS).status_code == 503
    assert client.get(path).json()['status'] == 'RESERVATION_PENDING'
    monkeypatch.setattr(broker, 'publish', original)
    assert client.post('/reservations/sync-events').json() == {'published_messages': 1}
    assert client.post(RESULTS).status_code == 200
    assert client.get(path).json()['status'] == 'RESERVED'
    assert client.post(REQUESTS).json()['unique_events_processed'] == 0
    assert len(stock(client)['reservations']) == 1


def test_partial_allocation_is_rolled_back_but_rejection_is_saved(client):
    path = order(client, items=[{'sku': 'SKU-001', 'quantity': 4}, {'sku': 'ZZ-MISSING', 'quantity': 1}])
    reserve(client, path)
    assert client.post(REQUESTS).status_code == 200
    assert stock(client)['reservations'] == []
    with client.app.state.allocation_service.sessions() as db:
        assert db.scalar(select(func.count()).select_from(ReservationBatchRow)) == 0
        assert db.scalar(select(func.count()).select_from(ProcessedEventRow)) == 1
    assert client.post(RESULTS).status_code == 200
    assert client.get(path).json()['status'] == 'RESERVATION_REJECTED'


def test_cache_failure_after_inventory_commit_retries_without_allocation(client):
    path = order(client)
    reserve(client, path)
    client.app.state.redis.fail.add('delete')
    assert client.post(REQUESTS).status_code == 503
    assert len(stock(client)['reservations']) == 1
    client.app.state.redis.fail.clear()
    assert client.post(REQUESTS).json()['unique_events_processed'] == 0
    assert client.post(RESULTS).status_code == 200
    assert len(stock(client)['reservations']) == 1


def test_cache_failure_after_outbound_commit_is_repaired(client):
    path = order(client)
    reserve(client, path)
    client.post(REQUESTS)
    client.app.state.redis.fail.add('delete')
    assert client.post(RESULTS).status_code == 503
    client.app.state.redis.fail.clear()
    assert client.post(RESULTS).json() == {'consumed_messages': 1, 'unique_events_processed': 0}
    assert client.get(path).json()['status'] == 'RESERVED'


def test_database_failure_rolls_back_and_keeps_request(client, monkeypatch):
    path = order(client)
    reserve(client, path)
    allocation = client.app.state.allocation_service
    original = allocation.reserve_in_transaction

    def fail(db, event):
        original(db, event)
        db.flush()
        raise OperationalError('simulated failure', {}, Exception('disk failure'))

    monkeypatch.setattr(allocation, 'reserve_in_transaction', fail)
    assert client.post(REQUESTS).status_code == 503
    assert stock(client)['reservations'] == []
    monkeypatch.setattr(allocation, 'reserve_in_transaction', original)
    assert client.post(REQUESTS).json()['unique_events_processed'] == 1
    assert client.post(RESULTS).status_code == 200


def test_duplicate_request_survives_restart(app_factory):
    broker = InMemoryEventBroker()
    with TestClient(app_factory(event_broker=broker)) as first:
        path = order(first)
        reserve(first, path)
        request = broker.published[-1][1]
        first.post(REQUESTS)
    broker.publish(RESERVATION_REQUEST_KEY, request)
    with TestClient(app_factory(event_broker=broker)) as second:
        assert second.post(REQUESTS).json()['unique_events_processed'] == 0
        assert len(stock(second)['reservations']) == 1
        assert second.post(RESULTS).json()['unique_events_processed'] == 1


def test_parallel_attempts_cannot_overbook(app_factory):
    broker = InMemoryEventBroker()
    with ExitStack() as stack:
        first, second = [stack.enter_context(TestClient(app_factory(event_broker=broker))) for _ in range(2)]
        paths = [order(first, 6), order(first, 6)]
        for path in paths:
            reserve(first, path)
        requests = [payload for key, payload in broker.published if key == RESERVATION_REQUEST_KEY]
        barrier = Barrier(2)

        def handle(client, payload):
            barrier.wait(timeout=5)
            return client.app.state.reservation_event_service.handle(payload)

        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [pool.submit(handle, client, payload) for client, payload in zip((first, second), requests)]
            assert all(job.result(timeout=10) for job in jobs)
        assert sum(r['quantity'] for r in stock(first)['reservations']) == 6
        assert first.post(RESULTS).json()['unique_events_processed'] == 2
        assert sorted(first.get(path).json()['status'] for path in paths) == ['RESERVATION_REJECTED', 'RESERVED']


def test_parallel_delivery_of_same_attempt_applies_once(app_factory):
    broker = InMemoryEventBroker()
    with ExitStack() as stack:
        clients = [stack.enter_context(TestClient(app_factory(event_broker=broker))) for _ in range(2)]
        path = order(clients[0])
        reserve(clients[0], path)
        payload = broker.published[-1][1]
        barrier = Barrier(2)

        def handle(client):
            barrier.wait(timeout=5)
            return client.app.state.reservation_event_service.handle(payload)

        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [pool.submit(handle, client) for client in clients]
            assert sorted(job.result(timeout=10) for job in jobs) == [False, True]
        assert len(stock(clients[0])['reservations']) == 1


def test_reusing_event_id_with_changed_payload_is_rejected(client):
    path = order(client)
    reserve(client, path)
    broker = client.app.state.event_broker
    payload = broker.published[-1][1]
    assert client.post(REQUESTS).status_code == 200
    broker.publish(RESERVATION_REQUEST_KEY, {**payload, 'items': [{'sku': 'SKU-001', 'quantity': 5}]})
    assert client.post(REQUESTS).status_code == 409
    assert sum(r['quantity'] for r in stock(client)['reservations']) == 4
