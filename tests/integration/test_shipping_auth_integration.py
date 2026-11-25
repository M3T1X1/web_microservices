"""Cztery usługi, prawdziwe HTTP przez Gateway i przekazywanie JWT między nimi."""
from uuid import uuid4
import pytest

pytestmark = pytest.mark.integration


def test_auth_create_login_me_and_role_enforcement(gateway_client):
    email, password = f"shipping-{uuid4().hex}@example.com", f"Test-{uuid4().hex}"
    response = gateway_client.post('/api/auth/users', json={'email': email, 'password': password, 'role': 'SHIPPING'})
    assert response.status_code == 201, response.text
    user = response.json()
    assert 'password' not in user and 'password_hash' not in user
    response = gateway_client.post('/api/auth/login', json={'email': email, 'password': password})
    assert response.status_code == 200
    assert response.headers['x-gateway'] == 'lab4'
    headers = {'Authorization': f"Bearer {response.json()['access_token']}"}
    assert gateway_client.get('/api/auth/me', headers=headers).json() == user
    assert gateway_client.get('/api/shipments', headers=headers).status_code == 200
    assert gateway_client.get('/api/stocks', headers=headers).status_code == 403
    assert gateway_client.post('/api/auth/users', headers=headers,
        json={'email': f'{uuid4().hex}@example.com', 'password': password, 'role': 'ADMIN'}).status_code == 403


def test_full_reservation_packing_shipping_via_gateway(gateway_client, stock):
    body = {'recipient_name': 'Test wysyłki', 'recipient_phone': '123456789', 'recipient_address': 'Adres testowy',
            'items': [{'sku': stock['sku'], 'quantity': 2}]}
    response = gateway_client.post('/api/orders', json=body)
    assert response.status_code == 201
    order_path = f"/api/orders/{response.json()['id']}"
    order_id = response.json()['id']
    response = gateway_client.post(order_path + '/reserve')
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'RESERVED'
    assert gateway_client.post(order_path + '/pack').json()['status'] == 'PACKED'
    gateway_client.get(order_path)
    assert gateway_client.get(order_path).headers['x-cache'] == 'HIT'
    response = gateway_client.post('/api/shipments', json={'order_id': order_id, 'destination_address': 'Warszawa, Testowa 1', 'courier_id': '22222222-2222-4222-8222-222222222222', 'weight_kg': 1.5})
    assert response.status_code == 201, response.text
    path = f"/api/shipments/{response.json()['id']}"
    assert gateway_client.post(path + '/ready').json()['status'] == 'READY'
    response = gateway_client.post(path + '/hand-over')
    assert response.status_code == 200, response.text
    assert response.headers['x-gateway'] == 'lab4'
    assert response.json()['status'] == 'HANDED_OVER'
    assert response.json()['handed_over_at'] is not None
    assert response.json()['notification_pending'] is False
    pending = gateway_client.get(order_path)
    assert pending.json()['status'] == 'PACKED' and pending.headers['x-cache'] == 'HIT'
    consumed = gateway_client.post('/api/orders/consume-events')
    assert consumed.status_code == 200, consumed.text
    assert consumed.json()['unique_events_processed'] >= 1
    read = gateway_client.get(order_path)
    assert read.json()['status'] == 'SHIPPED' and read.headers['x-cache'] == 'MISS'
    assert gateway_client.post(path + '/hand-over').status_code == 409
    assert gateway_client.get(f"/api/stocks/{stock['id']}").json()['reservations'] == [{'order_id': order_id, 'quantity': 2}]


def test_protected_endpoints_reject_missing_or_invalid_jwt(gateway_client):
    for path in ('/api/stocks', '/api/orders', '/api/shipments', '/api/auth/me'):
        for authorization in ('', 'Bearer invalid'):
            response = gateway_client.get(path, headers={'Authorization': authorization})
            assert response.status_code == 401
            assert response.headers['x-gateway'] == 'lab4'


def test_shipping_rejects_unpacked_order_without_creating_shipment(gateway_client, stock):
    order = gateway_client.post('/api/orders', json={'recipient_name': 'A', 'recipient_phone': '1',
        'recipient_address': 'B', 'items': [{'sku': stock['sku'], 'quantity': 1}]}).json()
    before = gateway_client.get('/api/shipments').json()
    response = gateway_client.post('/api/shipments', json={'order_id': order['id'], 'destination_address': 'Warszawa, Testowa 1', 'courier_id': '22222222-2222-4222-8222-222222222222', 'weight_kg': 1})
    assert response.status_code == 409
    assert response.json() == {'detail': 'Order must be packed before shipping'}
    assert gateway_client.get('/api/shipments').json() == before
