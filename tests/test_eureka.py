from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app.eureka import create_eureka_client
from tests.fakes import ControlledRedis, FakeEurekaClient


@pytest.mark.parametrize("service", ["inventory", "outbound"])
def test_eureka_lifecycle_runs_once_per_application(app_factory, service):
    registry = FakeEurekaClient()
    app = app_factory(service=service, eureka_client=registry)
    with TestClient(app) as client:
        assert app.state.eureka_client is registry
        assert registry.events == ["start"]
        assert client.get("/health").status_code == 200
        assert client.get("/ready").status_code == 200
        assert registry.events == ["start"]
    assert registry.events == ["start", "stop"]
    assert app.state.redis.closed


@pytest.mark.parametrize("service", ["inventory", "outbound"])
def test_eureka_failure_stops_client_before_closing_storage(app_factory, monkeypatch, service):
    events = []
    redis = ControlledRedis()
    close_redis = redis.close

    def close():
        events.append("close storage")
        close_redis()

    monkeypatch.setattr(redis, "close", close)

    class FailingEureka:
        async def start(self):
            assert app.state.engine is not None
            assert not redis.closed
            events.append("start")
            raise RuntimeError("Eureka startup failed")

        async def stop(self):
            assert not redis.closed
            events.append("stop")

    app = app_factory(service=service, redis_client=redis, eureka_client=FailingEureka())
    with pytest.raises(RuntimeError, match="Eureka startup failed"):
        with TestClient(app):
            pass
    assert events == ["start", "stop", "close storage"]
    assert redis.closed


@pytest.mark.parametrize("service", ["inventory", "outbound"])
def test_eureka_uses_configuration_for_its_own_service(monkeypatch, service):
    prefix = service.upper()
    monkeypatch.setenv("EUREKA_SERVER_URL", "http://registry.test:8761")
    monkeypatch.setenv("INSTANCE_IP", "192.0.2.10")
    monkeypatch.setenv("INSTANCE_HOST", "warehouse.test")
    monkeypatch.setenv(f"{prefix}_SERVICE_NAME", f"CUSTOM-{prefix}")
    monkeypatch.setenv(f"{prefix}_SERVICE_PORT", "8201")
    constructor = Mock()
    monkeypatch.setattr("app.eureka.EurekaClient", constructor)
    assert create_eureka_client(service, 8101) is constructor.return_value
    constructor.assert_called_once_with(
        eureka_server="http://registry.test:8761", eureka_context="/eureka",
        app_name=f"CUSTOM-{prefix}", instance_port=8201,
        instance_ip="192.0.2.10", instance_host="warehouse.test",
        home_page_url="/docs", status_page_url="/ready", health_check_url="/health",
        strict_service_error_policy=False,
    )
