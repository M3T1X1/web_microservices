from uuid import UUID

import pytest


@pytest.fixture
def order_body():
    return {
        "recipient_name": "Jan Kowalski",
        "recipient_phone": "123456789",
        "recipient_address": "ul. Polna 1, Warszawa",
        "items": [{"sku": "SKU-001", "quantity": 3}],
    }


def test_write_and_read(client, order_body):
    response = client.post("/orders", json=order_body)
    assert response.status_code == 201
    order = response.json()
    assert order == {**order_body, "id": order["id"], "status": "NEW"}

    response = client.get(f"/orders/{order['id']}")
    assert response.status_code == 200
    assert response.json() == order

    response = client.get("/orders")
    assert response.status_code == 200
    assert response.json() == [order]


def test_invalid_data_does_not_change_state(client, order_body):
    response = client.get("/orders")
    assert response.status_code == 200
    before = response.json()
    order_body["items"][0]["quantity"] = 0

    response = client.post("/orders", json=order_body)
    assert response.status_code == 422
    assert any(
        error["loc"] == ["body", "items", 0, "quantity"]
        for error in response.json()["detail"]
    )
    response = client.get("/orders")
    assert response.status_code == 200
    assert response.json() == before


def test_packing_without_reservation_does_not_change_state(client, order_body):
    response = client.post("/orders", json=order_body)
    assert response.status_code == 201
    order = response.json()
    assert order["status"] == "NEW"
    path = f"/orders/{order['id']}"

    response = client.post(f"{path}/pack")
    assert response.status_code == 409
    assert response.json() == {"detail": "Order must be reserved before packing"}

    response = client.get(path)
    assert response.status_code == 200
    assert response.json() == order


@pytest.mark.parametrize(
    ("order_id", "status"),
    [("99999999-9999-4999-8999-999999999999", 404), ("invalid", 422)],
)
def test_invalid_order_id_preserves_state(client, order_body, order_id, status):
    assert client.post("/orders", json=order_body).status_code == 201
    before = client.get("/orders").json()
    path = f"/orders/{order_id}"
    assert client.get(path).status_code == status
    response = client.post(f"{path}/pack")
    assert response.status_code == status
    if status == 404:
        assert response.json() == {"detail": "Order not found"}
    assert client.get("/orders").json() == before


@pytest.mark.parametrize("quantity", [-1, 1.5, "3", True])
def test_invalid_quantity_preserves_existing_orders(client, order_body, quantity):
    assert client.post("/orders", json=order_body).status_code == 201
    before = client.get("/orders").json()
    order_body["items"][0]["quantity"] = quantity
    assert client.post("/orders", json=order_body).status_code == 422
    assert client.get("/orders").json() == before


def test_reserved_order_can_be_packed_only_once(client, order_body):
    response = client.post("/orders", json=order_body)
    assert response.status_code == 201
    order_id = response.json()["id"]
    # Potwierdzenie z Inventory jest symulowane, bo integracja jeszcze nie istnieje.
    client.app.state.orders[UUID(order_id)].status = "RESERVED"
    path = f"/orders/{order_id}"
    before = client.get(path).json()

    response = client.post(f"{path}/pack")
    assert response.status_code == 200
    packed = {**before, "status": "PACKED"}
    assert response.json() == packed
    assert client.post(f"{path}/pack").status_code == 409
    assert client.get(path).json() == packed
