import os
import json
from pathlib import Path
import subprocess
from uuid import uuid4

import httpx
import pytest


@pytest.fixture
def publish_event():
    def publish(routing_key, payload):
        if os.getenv("LAB6_CONTAINERS") == "1":
            # Wstrzyknięcie duplikatu do testu bez publikacji AMQP na hoście.
            code = ("import json,sys; from app.messaging import RabbitEventBroker; "
                    "key,payload=json.load(sys.stdin); broker=RabbitEventBroker(); "
                    "broker.publish(key,payload); broker.close()")
            subprocess.run(["docker", "compose", "exec", "-T", "outbound", "python", "-c", code],
                           input=json.dumps([routing_key, payload]), text=True, check=True,
                           cwd=Path(__file__).resolve().parents[2], timeout=30)
        else:
            from app.messaging import RabbitEventBroker
            broker = RabbitEventBroker()
            try:
                broker.publish(routing_key, payload)
            finally:
                broker.close()
    return publish


@pytest.fixture
def gateway_client():
    token = os.getenv("GATEWAY_TOKEN")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    with httpx.Client(headers=headers, base_url=os.getenv("GATEWAY_URL", "http://127.0.0.1:8080"),
                      timeout=15, trust_env=False) as client:
        yield client


@pytest.fixture
def stock(gateway_client):
    sku = f"LAB4-{uuid4().hex}"
    response = gateway_client.post("/api/receipts", json={
        "supplier": "Test integracyjny",
        "items": [{"sku": sku, "target_location": "INTEGRATION-TEST", "quantity_received": 10}],
    })
    assert response.status_code == 201, response.text
    path = f"/api/receipts/{response.json()['id']}"
    assert gateway_client.post(path + "/accept").status_code == 200
    assert gateway_client.post(path + "/post").status_code == 200
    return next(item for item in gateway_client.get('/api/stocks').json() if item['sku'] == sku)


@pytest.fixture
def reserve_order(gateway_client):
    def reserve(path):
        response = gateway_client.post(path + '/reserve')
        assert response.status_code == 202, response.text
        assert response.json()['status'] == 'RESERVATION_PENDING'
        assert gateway_client.post(path + '/pack').status_code == 409
        assert gateway_client.post('/api/reservations/consume-events').status_code == 200
        assert gateway_client.get(path).json()['status'] == 'RESERVATION_PENDING'
        assert gateway_client.post('/api/orders/consume-reservation-events').status_code == 200
        response = gateway_client.get(path)
        assert response.json()['status'] == 'RESERVED'
        return response
    return reserve
