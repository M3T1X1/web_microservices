from urllib.error import HTTPError, URLError
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from py_eureka_client.eureka_client import WalkNodeException
from sqlalchemy import inspect

from app.inventory import create_inventory_app
from app.outbound import create_outbound_app
from tests.fakes import ControlledRedis


@pytest.mark.parametrize("domain,port", [("inventory", 8101), ("outbound", 8102), ("shipping", 8103), ("auth", 8104)])
def test_eureka_registration_has_service_addresses(monkeypatch, domain, port):
    from app import discovery

    for name in ("INSTANCE_IP", "INSTANCE_HOST", "SERVICE_PORT", "EUREKA_SERVER_URL"):
        monkeypatch.delenv(name, raising=False)
    captured = {}
    monkeypatch.setattr(discovery, "EurekaClient", lambda **kwargs: captured.update(kwargs))
    discovery.create_eureka_client(domain)
    assert captured["instance_ip"] == "127.0.0.1"
    assert captured["instance_host"] == "localhost"
    assert captured["instance_port"] == port
    assert captured["app_name"] == f"{domain.upper()}-SERVICE"
    assert (captured["home_page_url"], captured["status_page_url"], captured["health_check_url"]) == ("/docs", "/ready", "/health")
    assert captured["should_discover"] == (domain in {"outbound", "shipping"})


def test_eureka_registration_uses_configured_instance(monkeypatch):
    from app import discovery

    monkeypatch.setenv("INSTANCE_IP", "192.168.1.20")
    monkeypatch.setenv("INSTANCE_HOST", "warehouse.local")
    monkeypatch.setenv("SERVICE_PORT", "8120")
    monkeypatch.setenv("EUREKA_SERVER_URL", "http://registry.local:8761")
    captured = {}
    monkeypatch.setattr(discovery, "EurekaClient", lambda **kwargs: captured.update(kwargs))
    discovery.create_eureka_client("outbound")
    assert captured["instance_ip"] == "192.168.1.20"
    assert captured["instance_host"] == "warehouse.local"
    assert captured["instance_port"] == 8120
    assert captured["eureka_server"] == "http://registry.local:8761"


class FakeEureka:
    def __init__(self):
        self.started = self.stopped = False
        self.error = None
        self.calls = []
        self.stock = {"id": str(uuid4()), "sku": "SKU-001", "location": "A-01",
                      "quantity": 10, "reservations": [{"order_id": str(uuid4()), "quantity": 3}]}

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True

    async def do_service(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.error:
            raise self.error
        return self.stock


@pytest.fixture
def outbound(tmp_path):
    discovery = FakeEureka()
    app = create_outbound_app(
        outbound_database_url=f"sqlite:///{tmp_path}/outbound.db",
        redis_client=ControlledRedis(), eureka_client=discovery, auth_enabled=False,
    )
    with TestClient(app) as client:
        assert discovery.started
        yield client, discovery
    assert discovery.stopped


def test_stock_check_uses_logical_name_and_three_second_timeout(outbound):
    client, discovery = outbound
    response = client.get(f"/orders/stock-check/{discovery.stock['id']}")
    assert response.status_code == 200
    assert response.json() == {"stock": discovery.stock, "available_quantity": 7}
    assert discovery.calls == [(("INVENTORY-SERVICE", f"/stocks/{discovery.stock['id']}"),
                               {"return_type": "json", "prefer_ip": True, "timeout": 3})]


@pytest.mark.parametrize("error,status,detail", [
    (HTTPError("http://inventory", 404, "missing", {}, None), 404, "Stock not found in Inventory"),
    (HTTPError("http://inventory", 500, "error", {}, None), 502, "Inventory Service returned an HTTP error"),
    (URLError("refused"), 503, "Inventory Service is unavailable"),
    (TimeoutError(), 503, "Inventory Service is unavailable"),
    (ConnectionError(), 503, "Inventory Service is unavailable"),
    (WalkNodeException("No instances", []), 503, "Inventory Service is unavailable"),
])
def test_dependency_errors_are_controlled_and_health_stays_up(outbound, error, status, detail):
    client, discovery = outbound
    discovery.error = error
    response = client.get(f"/orders/stock-check/{discovery.stock['id']}")
    assert response.status_code == status
    assert response.json() == {"detail": detail}
    assert client.get("/health").json() == {"status": "ok", "service": "OUTBOUND-SERVICE"}


def test_malformed_dependency_response_is_bad_gateway(outbound):
    client, discovery = outbound
    stock_id = discovery.stock['id']
    discovery.stock = {"unexpected": "data"}
    assert client.get(f"/orders/stock-check/{stock_id}").status_code == 502


def test_invalid_uuid_does_not_call_inventory(outbound):
    client, discovery = outbound
    assert client.get("/orders/stock-check/not-a-uuid").status_code == 422
    assert discovery.calls == []


def test_separate_process_apps_only_initialize_owned_data(tmp_path):
    for domain, factory, own_routes, foreign_route, tables in [
        ("inventory", create_inventory_app, ("/stocks", "/receipts"), "/orders", {"stocks", "receipts", "reservation_batches"}),
        ("outbound", create_outbound_app, ("/orders",), "/stocks", {"orders", "order_dispatches"}),
    ]:
        discovery = FakeEureka()
        inventory_db, outbound_db = tmp_path / f"{domain}-i.db", tmp_path / f"{domain}-o.db"
        app = factory(database_url=f"sqlite:///{inventory_db}", outbound_database_url=f"sqlite:///{outbound_db}",
                      redis_client=ControlledRedis(), eureka_client=discovery, auth_enabled=False)
        with TestClient(app) as client:
            assert all(client.get(route).status_code == 200 for route in own_routes)
            assert client.get(foreign_route).status_code == 404
            assert set(app.state.databases) == {domain}
            assert set(inspect(app.state.databases[domain]).get_table_names()) == tables
        assert discovery.started and discovery.stopped
        assert inventory_db.exists() == (domain == "inventory")
        assert outbound_db.exists() == (domain == "outbound")
