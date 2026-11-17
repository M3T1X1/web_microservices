import pytest
from fastapi.testclient import TestClient


@pytest.mark.parametrize("service", ["inventory", "outbound"])
def test_health(app_factory, service):
    with TestClient(app_factory(service=service)) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "service": service}
