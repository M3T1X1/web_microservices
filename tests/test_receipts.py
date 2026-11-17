from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, inspect
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.db_models import StockRow


@pytest.fixture
def receipt_body():
    return {"supplier": "Dostawca ABC", "items": [
        {"sku": "SKU-001", "quantity_received": 5, "target_location": "A-01"},
    ]}


def accepted_receipt(client, body):
    response = client.post("/receipts", json=body)
    assert response.status_code == 201
    receipt = response.json()
    response = client.post(f"/receipts/{receipt['id']}/accept")
    assert response.status_code == 200
    assert response.json()["status"] == "ACCEPTED"
    return receipt["id"]


def test_receipt_lifecycle_updates_stock_and_preserves_reservations(client, receipt_body):
    stock = client.get("/stocks").json()[0]
    reservation = {"order_id": "22222222-2222-4222-8222-222222222222", "quantity": 7}
    assert client.post(f"/stocks/{stock['id']}/reservations", json=reservation).status_code == 201
    before = client.get("/stocks").json()
    receipt_body["items"] += [
        {"sku": "SKU-001", "quantity_received": 2, "target_location": "A-01"},
        {"sku": "SKU-001", "quantity_received": 4, "target_location": "B-02"},
    ]
    response = client.post("/receipts", json=receipt_body)
    assert response.status_code == 201
    receipt = response.json()
    assert UUID(receipt["id"]).version == 4
    assert receipt == {**receipt_body, "id": receipt["id"], "status": "NEW"}
    path = f"/receipts/{receipt['id']}"
    assert client.get(path).json() == receipt
    assert client.get("/receipts").json() == [receipt]
    assert client.get("/stocks").json() == before
    assert client.post(path + "/accept").json()["status"] == "ACCEPTED"
    assert client.get("/stocks").json() == before

    response = client.post(path + "/post")
    assert response.status_code == 200
    assert response.json() == {**receipt, "status": "POSTED"}
    stocks = {s["location"]: s for s in client.get("/stocks").json()}
    assert len(stocks) == 2
    assert stocks["A-01"] == {**before[0], "quantity": 17}
    assert stocks["B-02"]["sku"] == "SKU-001"
    assert stocks["B-02"]["quantity"] == 4
    assert stocks["B-02"]["reservations"] == []
    assert stocks["B-02"]["id"] != stock["id"]
    after = client.get("/stocks").json()
    for action in ("post", "accept", "cancel"):
        assert client.post(path + "/" + action).status_code == 409
    assert client.get("/stocks").json() == after
    assert client.get(path).json()["status"] == "POSTED"


def test_only_accepted_receipt_can_be_posted(client, receipt_body):
    receipt = client.post("/receipts", json=receipt_body).json()
    path = f"/receipts/{receipt['id']}"
    before = client.get("/stocks").json()
    response = client.post(path + "/post")
    assert response.status_code == 409
    assert response.json() == {"detail": "Only ACCEPTED receipts can be posted"}
    assert client.get(path).json() == receipt
    assert client.get("/stocks").json() == before


@pytest.mark.parametrize("accept_first", [False, True])
def test_cancel_before_posting_never_changes_stock(client, receipt_body, accept_first):
    receipt = client.post("/receipts", json=receipt_body).json()
    path = f"/receipts/{receipt['id']}"
    before = client.get("/stocks").json()
    if accept_first:
        assert client.post(path + "/accept").status_code == 200
        assert client.post(path + "/accept").status_code == 409
    response = client.post(path + "/cancel")
    assert response.status_code == 200
    assert response.json()["status"] == "CANCELLED"
    for action in ("accept", "post", "cancel"):
        assert client.post(path + "/" + action).status_code == 409
    assert client.get(path).json()["status"] == "CANCELLED"
    assert client.get("/stocks").json() == before


@pytest.mark.parametrize("quantity", [0, -1, 1.5, "3", True])
def test_invalid_receipt_quantity_preserves_data(client, receipt_body, quantity):
    before = client.get("/stocks").json()
    receipt_body["items"][0]["quantity_received"] = quantity
    assert client.post("/receipts", json=receipt_body).status_code == 422
    assert client.get("/receipts").json() == []
    assert client.get("/stocks").json() == before


@pytest.mark.parametrize("field", ["supplier", "sku", "target_location", "items"])
def test_required_receipt_data(client, receipt_body, field):
    if field == "items":
        receipt_body["items"] = []
    elif field == "supplier":
        receipt_body[field] = "   "
    else:
        receipt_body["items"][0][field] = "   "
    assert client.post("/receipts", json=receipt_body).status_code == 422
    assert client.get("/receipts").json() == []


@pytest.mark.parametrize(("receipt_id", "status"), [
    ("99999999-9999-4999-8999-999999999999", 404), ("invalid", 422),
])
def test_missing_or_invalid_receipt_id(client, receipt_id, status):
    path = f"/receipts/{receipt_id}"
    assert client.get(path).status_code == status
    for action in ("accept", "post", "cancel"):
        response = client.post(path + "/" + action)
        assert response.status_code == status
        if status == 404:
            assert response.json() == {"detail": "Receipt not found"}
    assert client.get("/receipts").json() == []
    assert client.get("/stocks").json()[0]["quantity"] == 10


def test_post_invalidates_all_changed_stock_after_commit(client, receipt_body, monkeypatch):
    extra = {"supplier": "ABC", "items": [
        {"sku": "SKU-002", "quantity_received": 2, "target_location": "A-01"},
    ]}
    extra_id = accepted_receipt(client, extra)
    assert client.post(f"/receipts/{extra_id}/post").status_code == 200
    before = client.get("/stocks").json()
    for stock in before:
        assert client.get(f"/stocks/{stock['id']}").headers["X-Cache"] == "MISS"
        assert client.get(f"/stocks/{stock['id']}").headers["X-Cache"] == "HIT"
    receipt_body["items"] += extra["items"]
    receipt_id = accepted_receipt(client, receipt_body)
    state = client.app.state
    observed = {}
    original_delete = state.stock_cache.delete

    def observe(stock_id):
        assert state.receipt_repository.get(UUID(receipt_id)).status == "POSTED"
        observed[str(stock_id)] = state.stock_repository.get(stock_id).quantity
        return original_delete(stock_id)

    monkeypatch.setattr(state.stock_cache, "delete", observe)
    assert client.post(f"/receipts/{receipt_id}/post").status_code == 200
    assert observed == {s["id"]: s["quantity"] + (5 if s["sku"] == "SKU-001" else 2) for s in before}
    for stock_id, quantity in observed.items():
        result = client.get(f"/stocks/{stock_id}")
        assert result.headers["X-Cache"] == "MISS"
        assert result.json()["quantity"] == quantity


def test_receipt_post_rolls_back_all_rows_on_commit_failure(client, receipt_body):
    receipt_body["items"].append(
        {"sku": "SKU-002", "quantity_received": 2, "target_location": "A-01"})
    receipt_id = accepted_receipt(client, receipt_body)
    before = client.get("/stocks").json()
    client.get(f"/stocks/{before[0]['id']}")
    state = client.app.state
    cached_before = dict(state.redis.values)

    def fail_commit(db):
        db.flush()
        raise SQLAlchemyError("Simulated failure after all receipt writes")

    event.listen(state.receipt_repository.sessions, "before_commit", fail_commit)
    try:
        response = client.post(f"/receipts/{receipt_id}/post")
        assert response.status_code == 503
        assert response.json() == {"detail": "Database unavailable"}
    finally:
        event.remove(state.receipt_repository.sessions, "before_commit", fail_commit)
    assert client.get("/stocks").json() == before
    assert client.get(f"/receipts/{receipt_id}").json()["status"] == "ACCEPTED"
    assert state.redis.values == cached_before
    assert client.post(f"/receipts/{receipt_id}/post").status_code == 200


def test_post_succeeds_when_redis_is_down(client, receipt_body):
    receipt_id = accepted_receipt(client, receipt_body)
    client.app.state.redis.down = True
    assert client.post(f"/receipts/{receipt_id}/post").status_code == 200
    stock = client.get("/stocks").json()[0]
    assert stock["quantity"] == 15
    assert client.get(f"/stocks/{stock['id']}").headers["X-Cache"] == "BYPASS"
    assert client.get(f"/receipts/{receipt_id}").json()["status"] == "POSTED"


def test_receipts_and_stock_survive_restart(app_factory, receipt_body):
    with TestClient(app_factory()) as client:
        receipt_id = accepted_receipt(client, receipt_body)
        receipt = client.post(f"/receipts/{receipt_id}/post").json()
        stocks = client.get("/stocks").json()
    with TestClient(app_factory()) as client:
        assert client.get(f"/receipts/{receipt_id}").json() == receipt
        assert client.get("/stocks").json() == stocks
        assert client.post(f"/receipts/{receipt_id}/post").status_code == 409


def test_concurrent_posting_of_one_receipt_counts_it_once(app_factory, receipt_body):
    with TestClient(app_factory()) as first, TestClient(app_factory()) as second:
        receipt_id = accepted_receipt(first, receipt_body)
        path = f"/receipts/{receipt_id}/post"
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda client: client.post(path).status_code, (first, second)))
        assert sorted(results) == [200, 409]
        assert first.get("/stocks").json()[0]["quantity"] == 15


def test_concurrent_receipts_for_new_sku_share_one_stock(app_factory, receipt_body):
    receipt_body["items"][0]["sku"] = "SKU-NEW"
    with TestClient(app_factory()) as first, TestClient(app_factory()) as second:
        ids = [accepted_receipt(first, receipt_body), accepted_receipt(first, receipt_body)]
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(c.post, f"/receipts/{rid}/post") for c, rid in zip((first, second), ids)]
            assert [f.result().status_code for f in futures] == [200, 200]
        stocks = [s for s in first.get("/stocks").json() if s["sku"] == "SKU-NEW"]
        assert len(stocks) == 1
        assert stocks[0]["quantity"] == 10


def test_database_rejects_duplicate_sku_location(client):
    sessions = client.app.state.stock_repository.sessions
    with pytest.raises(IntegrityError):
        with sessions.begin() as db:
            db.add(StockRow(id=UUID("33333333-3333-4333-8333-333333333333"),
                            sku="SKU-001", location="A-01", quantity=1, reservations=[]))
    assert len(client.get("/stocks").json()) == 1


def test_fresh_databases_create_tables_and_unique_stock_index(app_factory, tmp_path):
    assert not (tmp_path / "inventory.db").exists()
    assert not (tmp_path / "outbound.db").exists()
    with TestClient(app_factory()) as client:
        assert (tmp_path / "inventory.db").exists()
        assert not (tmp_path / "outbound.db").exists()
        inventory = inspect(client.app.state.engine)
        assert inventory.get_table_names() == ["receipts", "stocks"]
        stock = client.get("/stocks").json()[0]
        assert UUID(stock["id"]).version == 4
        assert stock["quantity"] == 10 and stock["reservations"] == []
        assert client.get("/receipts").json() == []
        assert any(i["name"] == "uq_stocks_sku_location" and i["unique"]
                   and i["column_names"] == ["sku", "location"]
                   for i in inventory.get_indexes("stocks"))
