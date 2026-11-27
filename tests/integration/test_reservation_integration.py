import pytest

pytestmark = pytest.mark.integration


def make_order(client, sku, quantity):
    response = client.post('/api/orders', json={'recipient_name': 'Rabbit reservation', 'recipient_phone': '1',
        'recipient_address': 'Test', 'items': [{'sku': sku, 'quantity': quantity}]})
    assert response.status_code == 201, response.text
    return '/api/orders/' + response.json()['id']


def test_rejection_and_deliberate_retry_via_real_broker(gateway_client, stock):
    client = gateway_client
    path = make_order(client, stock['sku'], 11)
    first = client.post(path + '/reserve')
    assert first.status_code == 202 and first.json()['status'] == 'RESERVATION_PENDING'
    assert client.post(path + '/pack').status_code == 409
    assert client.post('/api/reservations/consume-events').status_code == 200
    assert client.get(path).json()['status'] == 'RESERVATION_PENDING'
    assert client.post('/api/orders/consume-reservation-events').status_code == 200
    rejected = client.get(path).json()
    assert rejected['status'] == 'RESERVATION_REJECTED' and rejected['reservation_error']
    assert client.post('/api/reservations/consume-events').json()['consumed_messages'] == 0
    stock_path = '/api/stocks/' + stock['id']
    assert client.patch(stock_path, json={'quantity': 12}).status_code == 200
    second = client.post(path + '/reserve')
    assert second.status_code == 202
    assert second.json()['reservation_attempt_id'] != first.json()['reservation_attempt_id']
    assert client.post('/api/reservations/consume-events').status_code == 200
    assert client.post('/api/orders/consume-reservation-events').status_code == 200
    assert client.get(path).json()['status'] == 'RESERVED'
    assert sum(r['quantity'] for r in client.get(stock_path).json()['reservations']) == 11
    assert client.post(path + '/pack').json()['status'] == 'PACKED'


def test_repeated_pending_request_is_one_attempt(gateway_client, stock):
    client = gateway_client
    path = make_order(client, stock['sku'], 2)
    first, second = [client.post(path + '/reserve') for _ in range(2)]
    assert first.status_code == second.status_code == 202
    assert first.json()['reservation_attempt_id'] == second.json()['reservation_attempt_id']
    assert client.post('/api/reservations/consume-events').json() == {'consumed_messages': 1, 'unique_events_processed': 1}
    assert client.post('/api/orders/consume-reservation-events').json() == {'consumed_messages': 1, 'unique_events_processed': 1}
    assert client.post(path + '/reserve').json()['status'] == 'RESERVED'
    assert client.post('/api/reservations/consume-events').json()['consumed_messages'] == 0
