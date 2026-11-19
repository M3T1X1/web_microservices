import os
from uuid import uuid4

import httpx
import pytest


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
