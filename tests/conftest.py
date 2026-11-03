import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.fakes import ControlledRedis


@pytest.fixture
def app_factory(tmp_path):
    def factory(**kwargs):
        options = {
            "database_url": "sqlite:///" + (tmp_path / "inventory.db").as_posix(),
            "outbound_database_url": "sqlite:///" + (tmp_path / "outbound.db").as_posix(),
            "redis_client": ControlledRedis(),
            "cache_prefix": f"lab2:test:{tmp_path.name}:warehouse",
            "cache_ttl": 60,
        }
        options.update(kwargs)
        return create_app(**options)
    return factory


@pytest.fixture
def client(app_factory):
    # Wzorzec tmp_path i atrapy Redis z ZIP, dwie bazy własnej domeny.
    with TestClient(app_factory()) as test_client:
        yield test_client
