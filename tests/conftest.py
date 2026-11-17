import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.outbound import create_app as create_outbound_app
from tests.fakes import ControlledRedis


@pytest.fixture
def app_factory(tmp_path):
    def factory(service="inventory", **kwargs):
        options = {
            "database_url": "sqlite:///" + (tmp_path / f"{service}.db").as_posix(),
            "redis_client": ControlledRedis(),
            "cache_prefix": f"lab2:test:{tmp_path.name}:warehouse",
            "cache_ttl": 60,
        }
        options.update(kwargs)
        factories = {"inventory": create_app, "outbound": create_outbound_app}
        return factories[service](**options)
    return factory


@pytest.fixture
def client(app_factory):
    with TestClient(app_factory()) as test_client:
        yield test_client


@pytest.fixture
def outbound_client(app_factory):
    with TestClient(app_factory(service="outbound")) as test_client:
        yield test_client
