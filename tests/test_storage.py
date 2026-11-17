"""Scenariusze test_storage.py z ZIP LAB2 przeniesione na własne API HTTP."""
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, inspect
from sqlalchemy.exc import SQLAlchemyError

from app.db_models import OrderRow
from app.models.order import Order


ORDER_BODY = {"recipient_name": "Test LAB2", "recipient_phone": "123456789",
              "recipient_address": "Magazyn testowy", "items": [{"sku": "SKU-001", "quantity": 3}]}
RESERVATION = {"order_id": "22222222-2222-4222-8222-222222222222", "quantity": 3}


def stock_path(client):
    return "/stocks/" + client.get("/stocks").json()[0]["id"]


def test_both_domains_survive_restart_with_empty_cache(app_factory):
    first = app_factory()
    first_outbound = app_factory(service="outbound")
    with TestClient(first) as client, TestClient(first_outbound) as outbound:
        path = stock_path(client)
        assert client.post(path + "/reservations", json=RESERVATION).status_code == 201
        assert client.patch(path, json={"quantity": 14}).status_code == 200
        stock = client.get(path).json()
        order = outbound.post("/orders", json=ORDER_BODY).json()
        order_path = "/orders/" + order["id"]
        assert outbound.get(order_path).json() == order
    assert first.state.redis.closed
    assert first_outbound.state.redis.closed
    with TestClient(app_factory()) as client, TestClient(app_factory(service="outbound")) as outbound:
        for api, resource_path, expected in ((client, path, stock), (outbound, order_path, order)):
            response = api.get(resource_path)
            assert response.status_code == 200
            assert response.headers["X-Cache"] == "MISS"
            assert response.json() == expected
        assert client.get("/stocks").json() == [stock]
        assert outbound.get("/orders").json() == [order]


@pytest.mark.parametrize("domain", ["stock", "order"])
def test_miss_hit_skips_repository(client, outbound_client, monkeypatch, domain):
    client = client if domain == "stock" else outbound_client
    path = (stock_path(client) if domain == "stock" else
            "/orders/" + client.post("/orders", json=ORDER_BODY).json()["id"])
    repository = getattr(client.app.state, domain + "_repository")
    cache = getattr(client.app.state, domain + "_cache")
    original = repository.get
    reads = []

    def counted(resource_id):
        reads.append(resource_id)
        return original(resource_id)

    monkeypatch.setattr(repository, "get", counted)
    first, second = client.get(path), client.get(path)
    assert first.status_code == second.status_code == 200
    assert first.headers["X-Cache"] == "MISS"
    assert second.headers["X-Cache"] == "HIT"
    assert first.json() == second.json()
    assert len(reads) == 1
    assert client.app.state.redis.expirations[cache.key(UUID(first.json()["id"]))] == 60


def test_reservation_and_update_invalidate_only_after_commit(client, monkeypatch):
    path = stock_path(client)
    state = client.app.state
    resource_id = UUID(path.rsplit("/", 1)[1])
    assert client.get(path).headers["X-Cache"] == "MISS"
    assert client.get(path).headers["X-Cache"] == "HIT"
    observed = []
    original_delete = state.stock_cache.delete

    def observe(stock_id):
        observed.append(state.stock_repository.get(stock_id))
        return original_delete(stock_id)

    monkeypatch.setattr(state.stock_cache, "delete", observe)
    assert client.post(path + "/reservations", json=RESERVATION).status_code == 201
    assert observed[-1].reservations[0].quantity == 3
    assert state.redis.get(state.stock_cache.key(resource_id)) is None
    result = client.get(path)
    assert result.headers["X-Cache"] == "MISS"
    assert result.json()["reservations"] == [RESERVATION]
    assert client.patch(path, json={"quantity": 15}).status_code == 200
    assert observed[-1].quantity == 15
    result = client.get(path)
    assert result.headers["X-Cache"] == "MISS"
    assert result.json()["quantity"] == 15


def test_pack_invalidates_order_cache(outbound_client):
    client = outbound_client
    order = client.post("/orders", json=ORDER_BODY).json()
    order_id = UUID(order["id"])
    state = client.app.state
    with state.order_repository.sessions.begin() as db:
        db.get(OrderRow, order_id).status = "RESERVED"
    path = "/orders/" + str(order_id)
    assert client.get(path).json()["status"] == "RESERVED"
    assert client.get(path).headers["X-Cache"] == "HIT"
    assert client.post(path + "/pack").json()["status"] == "PACKED"
    assert state.redis.get(state.order_cache.key(order_id)) is None
    result = client.get(path)
    assert result.headers["X-Cache"] == "MISS"
    assert result.json()["status"] == "PACKED"


@pytest.mark.parametrize("operation", ["get", "set", "delete", "all"])
def test_cache_outage_does_not_block_read_or_writes(client, outbound_client, operation):
    path = stock_path(client)
    state = client.app.state
    for redis in (state.redis, outbound_client.app.state.redis):
        if operation == "all":
            redis.down = True
        else:
            redis.fail.add(operation)
    result = client.get(path)
    assert result.status_code == 200
    assert result.headers["X-Cache"] == ("MISS" if operation == "delete" else "BYPASS")
    assert client.post(path + "/reservations", json=RESERVATION).status_code == 201
    assert client.patch(path, json={"quantity": 18}).status_code == 200
    stock = state.stock_repository.get(UUID(path.rsplit("/", 1)[1]))
    assert stock.quantity == 18 and stock.reservations[0].quantity == 3
    response = outbound_client.post("/orders", json=ORDER_BODY)
    assert response.status_code == 201
    order_id = UUID(response.json()["id"])
    order_response = outbound_client.get(f"/orders/{order_id}")
    assert order_response.status_code == 200
    assert order_response.headers["X-Cache"] == ("MISS" if operation == "delete" else "BYPASS")
    order_repository = outbound_client.app.state.order_repository
    with order_repository.sessions.begin() as db:
        db.get(OrderRow, order_id).status = "RESERVED"
    assert outbound_client.post(f"/orders/{order_id}/pack").json()["status"] == "PACKED"
    assert order_repository.get(order_id).status == "PACKED"


def test_failed_invalidation_leaves_stale_value_until_ttl(client):
    path = stock_path(client)
    assert client.get(path).json()["quantity"] == 10
    fake = client.app.state.redis
    fake.down = True
    assert client.patch(path, json={"quantity": 20}).status_code == 200
    bypass = client.get(path)
    assert bypass.headers["X-Cache"] == "BYPASS" and bypass.json()["quantity"] == 20
    fake.down = False
    stale = client.get(path)
    assert stale.headers["X-Cache"] == "HIT" and stale.json()["quantity"] == 10
    fake.now += 60
    fresh = client.get(path)
    assert fresh.headers["X-Cache"] == "MISS" and fresh.json()["quantity"] == 20


def test_cached_availability_never_authorizes_reservation(client):
    path = stock_path(client)
    client.get(path)
    client.app.state.redis.fail.add("delete")
    assert client.post(path + "/reservations", json={**RESERVATION, "quantity": 7}).status_code == 201
    assert client.get(path).json()["reservations"] == []  # Stara kopia przy nieudanym DELETE.
    assert client.post(path + "/reservations", json={**RESERVATION, "quantity": 4}).status_code == 409
    assert client.app.state.stock_repository.list()[0].reservations[0].quantity == 7


def test_conflict_keeps_database_and_cache_unchanged(client):
    path = stock_path(client)
    assert client.post(path + "/reservations", json=RESERVATION).status_code == 201
    before = client.get(path).json()
    saved_cache = dict(client.app.state.redis.values)
    result = client.patch(path, json={"quantity": 2})
    assert result.status_code == 409
    assert result.json() == {"detail": "Quantity cannot be lower than reserved stock"}
    assert client.app.state.redis.values == saved_cache
    assert client.get("/stocks").json() == [before]


@pytest.mark.parametrize("quantity", [-1, 1.5, True, "5", None])
def test_invalid_quantity_update_is_422(client, quantity):
    path = stock_path(client)
    before = client.get(path).json()
    assert client.patch(path, json={"quantity": quantity}).status_code == 422
    assert client.get("/stocks").json() == [before]


@pytest.mark.parametrize("domain", ["stock", "order"])
def test_flushed_write_rolls_back_on_commit_error(client, outbound_client, domain):
    path = stock_path(client)
    before = client.get(path).json()
    target = client if domain == "stock" else outbound_client
    repository = getattr(target.app.state, domain + "_repository")

    def fail_commit(db):
        db.flush()  # Zapis faktycznie dotarł do bazy, ale jeszcze nie ma commit.
        raise SQLAlchemyError("Simulated failure after flush")

    event.listen(repository.sessions, "before_commit", fail_commit)
    try:
        response = (client.patch(path, json={"quantity": 99}) if domain == "stock"
                    else outbound_client.post("/orders", json=ORDER_BODY))
        assert response.status_code == 503
        assert response.json() == {"detail": "Database unavailable"}
        assert client.get("/stocks").json() == [before]
        assert outbound_client.get("/orders").json() == []
        assert client.get(path).headers["X-Cache"] == "HIT"
    finally:
        event.remove(repository.sessions, "before_commit", fail_commit)
    assert client.patch(path, json={"quantity": 12}).status_code == 200
    assert outbound_client.post("/orders", json=ORDER_BODY).status_code == 201


def test_known_duplicate_uuid_is_409_and_preserves_existing_order(outbound_client, monkeypatch):
    client = outbound_client
    first = client.post("/orders", json=ORDER_BODY).json()
    with monkeypatch.context() as patch:
        patch.setattr("app.repositories.Order", lambda **body: Order(id=UUID(first["id"]), **body))
        response = client.post("/orders", json={**ORDER_BODY, "recipient_name": "Inny odbiorca"})
    assert response.status_code == 409
    assert response.json() == {"detail": "Order already exists"}
    assert client.get("/orders").json() == [first]


def test_concurrent_reservations_across_app_instances(app_factory):
    with TestClient(app_factory()) as first, TestClient(app_factory()) as second:
        path = stock_path(first) + "/reservations"
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(c.post, path, json={**RESERVATION, "quantity": 7}) for c in (first, second)]
            assert sorted(f.result().status_code for f in futures) == [201, 409]
        assert first.get("/stocks").json()[0]["reservations"] == [{**RESERVATION, "quantity": 7}]


def test_database_ownership(client, outbound_client):
    assert inspect(client.app.state.engine).get_table_names() == ["receipts", "stocks"]
    assert inspect(outbound_client.app.state.engine).get_table_names() == ["orders"]
    with client.app.state.engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1


@pytest.mark.parametrize("payload", ["invalid json", '{"quantity": 1}', '[]'])
def test_corrupt_cache_is_rebuilt_from_database(client, payload):
    path = stock_path(client)
    state = client.app.state
    state.redis.set(state.stock_cache.key(UUID(path.rsplit("/", 1)[1])), payload, ex=60)
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["X-Cache"] == "MISS" and response.json()["quantity"] == 10
    assert client.get(path).headers["X-Cache"] == "HIT"


@pytest.mark.parametrize("service", ["inventory", "outbound"])
def test_ready_degraded_when_cache_down(app_factory, service):
    with TestClient(app_factory(service=service)) as client:
        assert client.get("/ready").json()["status"] == "ok"
        client.app.state.redis.down = True
        response = client.get("/ready")
        assert response.status_code == 200
        assert response.json() == {"service": service, "status": "degraded",
                                   "database": {service: "ok"}, "cache": "down"}
        assert client.get("/health").json() == {"status": "ok", "service": service}


def test_database_outage_returns_503(client, monkeypatch):
    path = stock_path(client)
    client.get(path)

    def unavailable(*args, **kwargs):
        raise SQLAlchemyError("Simulated database outage")

    monkeypatch.setattr(client.app.state.engine, "connect", unavailable)
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json()["database"] == {"inventory": "down"}
    assert client.post(path + "/reservations", json=RESERVATION).status_code == 503
    assert client.get("/stocks").status_code == 503
    assert client.get(path).headers["X-Cache"] == "HIT"
    assert client.get("/health").status_code == 200


def test_quantity_can_equal_reserved_and_missing_stock_is_404(client):
    path = stock_path(client)
    assert client.post(path + "/reservations", json=RESERVATION).status_code == 201
    assert client.patch(path, json={"quantity": 3}).json()["quantity"] == 3
    assert client.post(path + "/reservations", json={**RESERVATION, "quantity": 1}).status_code == 409
    before = client.get("/stocks").json()
    response = client.patch("/stocks/99999999-9999-4999-8999-999999999999", json={"quantity": 1})
    assert response.status_code == 404 and response.json() == {"detail": "Stock not found"}
    assert client.get("/stocks").json() == before
