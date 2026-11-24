"""Rzeczywisty RabbitMQ; uruchamiac na osobnych bazach i vhoscie testowym."""
import pytest

from app.models.events import SHIPMENT_ROUTING_KEY, ShipmentHandedOver
from app.models.shipment import Shipment

pytestmark = pytest.mark.integration


def test_duplicate_message_updates_order_once_and_invalidates_cache(gateway_client, stock, publish_event):
    client = gateway_client
    order_response = client.post('/api/orders', json={'recipient_name': 'RabbitMQ test', 'recipient_phone': '1',
        'recipient_address': 'Test', 'items': [{'sku': stock['sku'], 'quantity': 1}]})
    assert order_response.status_code == 201
    order = order_response.json()
    order_path = f"/api/orders/{order['id']}"
    assert client.post(order_path + '/reserve').status_code == 200
    assert client.post(order_path + '/pack').status_code == 200
    client.get(order_path)
    assert client.get(order_path).headers['x-cache'] == 'HIT'
    created = client.post('/api/shipments', json={'order_id': order['id'], 'carrier': 'Test', 'weight_kg': 1})
    assert created.status_code == 201
    shipment_path = f"/api/shipments/{created.json()['id']}"
    assert client.post(shipment_path + '/ready').status_code == 200
    handed = client.post(shipment_path + '/hand-over')
    assert handed.status_code == 200, handed.text
    assert client.get(order_path).json()['status'] == 'PACKED'
    event = ShipmentHandedOver.from_shipment(Shipment.model_validate(handed.json()))
    publish_event(SHIPMENT_ROUTING_KEY, {**event.model_dump(mode='json'), 'future_field': 'ignored'})
    consumed = client.post('/api/orders/consume-events')
    assert consumed.status_code == 200, consumed.text
    assert consumed.json() == {'consumed_messages': 2, 'unique_events_processed': 1}
    read = client.get(order_path)
    assert read.json()['status'] == 'SHIPPED' and read.headers['x-cache'] == 'MISS'
    assert read.headers['x-gateway'] == 'lab4'
    assert client.get(order_path).headers['x-cache'] == 'HIT'
