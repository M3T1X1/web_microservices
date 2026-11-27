"""Wyłącznie prawdziwe HTTP przez bramkę; dane przygotowywane przez API Inventory."""
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration


def assert_gateway(response, status=200):
    assert response.status_code == status, response.text
    assert response.headers.get("x-gateway") == "lab4"


def test_inventory_read_has_gateway_and_cache_headers(gateway_client, stock):
    path = f"/api/stocks/{stock['id']}"
    first = gateway_client.get(path)
    assert_gateway(first)
    assert first.json() == stock
    assert first.headers["x-cache"] == "MISS"
    second = gateway_client.get(path)
    assert_gateway(second)
    assert second.headers["x-cache"] == "HIT"
    assert second.json() == stock


def test_outbound_read_executes_discovered_inventory_call(gateway_client, stock):
    response = gateway_client.get(f"/api/orders/stock-check/{stock['id']}")
    assert_gateway(response)
    assert response.json() == {"stock": stock, "available_quantity": 10}


def test_write_invalidates_cache_and_read_returns_committed_change(gateway_client, stock):
    path = f"/api/stocks/{stock['id']}"
    assert_gateway(gateway_client.get(path))
    assert gateway_client.get(path).headers["x-cache"] == "HIT"
    response = gateway_client.patch(path, json={"quantity": 14})
    assert_gateway(response)
    assert response.json()["quantity"] == 14
    response = gateway_client.get(path)
    assert_gateway(response)
    assert response.headers["x-cache"] == "MISS"
    assert response.json() == {**stock, "quantity": 14}
    assert gateway_client.get(path).headers["x-cache"] == "HIT"


def test_validation_and_conflict_are_forwarded_without_change(gateway_client, stock):
    path = f"/api/stocks/{stock['id']}"
    response = gateway_client.patch(path, json={"quantity": -1})
    assert_gateway(response, 422)
    assert response.json()["detail"][0]["loc"] == ["body", "quantity"]
    response = gateway_client.post(f"{path}/reservations", json={"order_id": str(uuid4()), "quantity": 11})
    assert_gateway(response, 409)
    assert response.json() == {"detail": "Insufficient available stock"}
    assert gateway_client.get(path).json() == stock


def test_domain_404_differs_from_missing_gateway_route(gateway_client):
    response = gateway_client.get(f"/api/stocks/{uuid4()}")
    assert_gateway(response, 404)
    assert response.json() == {"detail": "Stock not found"}
    response = gateway_client.get("/api/unknown-resource")
    assert response.status_code == 404
    assert "x-gateway" not in response.headers
    assert "detail" not in response.json()


def test_gateway_does_not_publish_technical_endpoints(gateway_client):
    response = gateway_client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"service": "API-GATEWAY", "status": "ok"}
    assert "x-gateway" not in response.headers
    for path in ("/api/health", "/api/ready", "/api/docs", "/api/openapi.json"):
        response = gateway_client.get(path)
        assert response.status_code == 404
        assert "x-gateway" not in response.headers


def test_swagger_schemas_use_gateway_and_only_publish_routed_operations(gateway_client):
    allowed = {
        "inventory": ("/stocks", "/receipts", "/reservations/consume-events", "/reservations/sync-events",
                      "/reservations/consume-shipment-events", "/reservations/consume-cancellation-events"),
        "outbound": ("/orders",),
        "shipping": ("/shipments",),
        "auth": ("/auth",),
    }
    for service, prefixes in allowed.items():
        docs_path = f"/docs/{service}"
        response = gateway_client.get(docs_path)
        assert_gateway(response)
        assert "SwaggerUIBundle" in response.text
        assert f"{docs_path}/openapi.json" in response.text
        response = gateway_client.get(f"{docs_path}/openapi.json")
        assert_gateway(response)
        schema = response.json()
        assert schema["servers"][0]["url"] == "/api"
        assert schema["paths"]
        assert all(path.startswith(prefixes) for path in schema["paths"])
        assert schema["components"]["securitySchemes"]["HTTPBearer"]["scheme"] == "bearer"


def test_outbound_order_write_and_read(gateway_client):
    body = {"recipient_name": "Test LAB4", "recipient_phone": "123456789",
            "recipient_address": f"Test {uuid4().hex}", "items": [{"sku": "SKU-001", "quantity": 1}]}
    response = gateway_client.post("/api/orders", json=body)
    assert_gateway(response, 201)
    order = response.json()
    assert order == {**body, "id": order["id"], "status": "NEW", "reservation_attempt_id": None,
                     "reservation_error": None, "reservation_notification_pending": False}
    response = gateway_client.get(f"/api/orders/{order['id']}")
    assert_gateway(response)
    assert response.json() == order
    assert response.headers["x-cache"] == "MISS"
