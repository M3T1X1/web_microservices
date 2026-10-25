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
