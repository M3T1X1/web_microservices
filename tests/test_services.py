import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError


def test_services_expose_only_their_own_routes(client, outbound_client):
    inventory_paths = client.get("/openapi.json").json()["paths"]
    outbound_paths = outbound_client.get("/openapi.json").json()["paths"]
    assert "/stocks" in inventory_paths and "/receipts" in inventory_paths
    assert "/orders" in outbound_paths
    assert "/orders" not in inventory_paths
    assert "/stocks" not in outbound_paths and "/receipts" not in outbound_paths
    assert client.get("/orders").status_code == 404
    assert outbound_client.get("/stocks").status_code == 404
    assert outbound_client.get("/receipts").status_code == 404


@pytest.mark.parametrize("service", ["inventory", "outbound"])
def test_service_starts_without_the_other_database(app_factory, tmp_path, monkeypatch, service):
    other_service = "outbound" if service == "inventory" else "inventory"
    other_variable = "OUTBOUND_DATABASE_URL" if service == "inventory" else "DATABASE_URL"
    monkeypatch.setenv(other_variable, "sqlite:///" + (tmp_path / "missing" / "other.db").as_posix())
    with TestClient(app_factory(service=service)) as client:
        assert client.get("/ready").json()["database"] == {service: "ok"}
        assert not (tmp_path / f"{other_service}.db").exists()


@pytest.mark.parametrize("failed_service", ["inventory", "outbound"])
def test_database_outage_is_limited_to_its_service(client, outbound_client, monkeypatch, failed_service):
    clients = {"inventory": client, "outbound": outbound_client}
    failed = clients.pop(failed_service)
    healthy = next(iter(clients.values()))

    def unavailable(*args, **kwargs):
        raise SQLAlchemyError("Simulated database outage")

    monkeypatch.setattr(failed.app.state.engine, "connect", unavailable)
    assert failed.get("/ready").status_code == 503
    assert healthy.get("/ready").status_code == 200
    assert healthy.get("/ready").json()["status"] == "ok"


def test_closing_inventory_keeps_outbound_running(app_factory):
    inventory_app = app_factory()
    outbound_app = app_factory(service="outbound")
    with TestClient(outbound_app) as outbound:
        with TestClient(inventory_app) as inventory:
            assert inventory.get("/health").status_code == 200
        assert inventory_app.state.redis.closed
        assert not outbound_app.state.redis.closed
        assert outbound.get("/orders").status_code == 200
        assert outbound.get("/ready").json()["status"] == "ok"
