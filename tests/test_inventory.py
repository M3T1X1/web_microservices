import pytest


ORDER_ID = "22222222-2222-4222-8222-222222222222"


def test_write_and_read(client):
    response = client.get("/stocks")
    assert response.status_code == 200
    stock = response.json()[0]
    path = f"/stocks/{stock['id']}"
    response = client.get(path)
    assert response.status_code == 200
    assert response.json() == stock

    body = {"order_id": ORDER_ID, "quantity": 3}
    response = client.post(f"{path}/reservations", json=body)
    assert response.status_code == 201
    assert response.json() == body
    response = client.get(path)
    assert response.status_code == 200
    assert response.json() == {**stock, "reservations": [body]}

    response = client.get("/stocks/99999999-9999-4999-8999-999999999999")
    assert response.status_code == 404
    assert response.json() == {"detail": "Stock not found"}


def test_invalid_data_does_not_change_state(client):
    stock = client.get("/stocks").json()[0]
    path = f"/stocks/{stock['id']}"
    response = client.post(f"{path}/reservations", json={"order_id": ORDER_ID, "quantity": 0})
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "quantity"]
    response = client.get(path)
    assert response.status_code == 200
    assert response.json() == stock


def test_conflict_does_not_change_state(client):
    stock = client.get("/stocks").json()[0]
    path = f"/stocks/{stock['id']}"
    body = {"order_id": ORDER_ID, "quantity": 7}
    response = client.post(f"{path}/reservations", json=body)
    assert response.status_code == 201
    assert response.json() == body

    response = client.post(f"{path}/reservations", json={
        "order_id": "33333333-3333-4333-8333-333333333333", "quantity": 4,
    })
    assert response.status_code == 409
    assert response.json() == {"detail": "Insufficient available stock"}
    response = client.get(path)
    assert response.status_code == 200
    assert response.json() == {**stock, "reservations": [body]}


@pytest.mark.parametrize("quantity", [0, -1, 1.5, "3", True])
def test_invalid_reservation_quantity_preserves_state(client, quantity):
    before = client.get("/stocks").json()
    path = f"/stocks/{before[0]['id']}/reservations"
    response = client.post(path, json={"order_id": ORDER_ID, "quantity": quantity})
    assert response.status_code == 422
    assert client.get("/stocks").json() == before


@pytest.mark.parametrize(
    ("stock_id", "status"),
    [("99999999-9999-4999-8999-999999999999", 404), ("invalid", 422)],
)
def test_invalid_stock_id_preserves_state(client, stock_id, status):
    before = client.get("/stocks").json()
    path = f"/stocks/{stock_id}"
    assert client.get(path).status_code == status
    response = client.post(
        f"{path}/reservations", json={"order_id": ORDER_ID, "quantity": 1}
    )
    assert response.status_code == status
    if status == 404:
        assert response.json() == {"detail": "Stock not found"}
    assert client.get("/stocks").json() == before


def test_reservation_can_use_exactly_available_stock(client):
    stock = client.get("/stocks").json()[0]
    path = f"/stocks/{stock['id']}"
    body = {"order_id": ORDER_ID, "quantity": stock["quantity"]}
    assert client.post(f"{path}/reservations", json=body).status_code == 201
    before = client.get(path).json()
    response = client.post(
        f"{path}/reservations", json={"order_id": ORDER_ID, "quantity": 1}
    )
    assert response.status_code == 409
    assert client.get(path).json() == before
