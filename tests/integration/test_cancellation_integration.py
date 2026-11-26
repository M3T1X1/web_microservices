import json
import os
from pathlib import Path
import subprocess

import pytest

from app.models.events import CANCELLATION_ROUTING_KEY, cancellation_event_id

pytestmark = pytest.mark.integration


def new_order(client, stock):
    response = client.post('/api/orders', json={'recipient_name': 'Cancellation integration', 'recipient_phone': '1',
        'recipient_address': 'Test', 'items': [{'sku': stock['sku'], 'quantity': 3}]})
    assert response.status_code == 201, response.text
    return response.json()['id']


def test_manual_cancellation_and_duplicate_keep_physical_stock(gateway_client, stock, reserve_order, publish_event):
    client = gateway_client
    order_id = new_order(client, stock)
    path = '/api/orders/' + order_id
    reserve_order(path)
    assert client.post(path + '/cancel').json()['status'] == 'CANCELLED'
    assert client.post(path + '/pack').status_code == 409
    before = client.get('/api/stocks/' + stock['id']).json()
    assert before['quantity'] == 10 and before['reservations'] == [{'order_id': order_id, 'quantity': 3}]
    assert client.post('/api/reservations/consume-cancellation-events').json() == {
        'consumed_messages': 1, 'unique_events_processed': 1}
    after = client.get('/api/stocks/' + stock['id']).json()
    assert after['quantity'] == 10 and after['reservations'] == []
    assert client.post(path + '/cancel').status_code == 200
    assert client.post(path + '/sync-cancellation').status_code == 200
    assert client.post('/api/reservations/consume-cancellation-events').json()['consumed_messages'] == 0
    if os.getenv('LAB6_CONTAINERS') == '1':
        code = ('import sys,json; from uuid import UUID; from app.database import make_database; '
                'from app.db_models import OutboxEventRow; import os; '
                'engine,sessions=make_database(os.environ["OUTBOUND_DATABASE_URL"]); '
                'db=sessions(); row=db.get(OutboxEventRow,UUID(sys.stdin.read())); print(json.dumps(row.payload)); db.close(); engine.dispose()')
        result = subprocess.run(['docker','compose','exec','-T','outbound','python','-c',code],
            input=str(cancellation_event_id(order_id)), text=True, check=True, capture_output=True,
            cwd=Path(__file__).resolve().parents[2], timeout=30)
        publish_event(CANCELLATION_ROUTING_KEY, json.loads(result.stdout))
        assert client.post('/api/reservations/consume-cancellation-events').json() == {
            'consumed_messages': 1, 'unique_events_processed': 0}
        assert client.get('/api/stocks/' + stock['id']).json() == after


def test_packed_order_cannot_be_cancelled(gateway_client, stock, reserve_order):
    order_id = new_order(gateway_client, stock)
    path = '/api/orders/' + order_id
    reserve_order(path)
    assert gateway_client.post(path + '/pack').status_code == 200
    assert gateway_client.post(path + '/cancel').status_code == 409
    assert gateway_client.get(path).json()['status'] == 'PACKED'
