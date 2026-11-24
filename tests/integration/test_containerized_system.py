"""Konfiguracja działających kontenerów, izolacja portów i rejestr Eureka."""
import json
import os
from pathlib import Path
import socket
import subprocess

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    os.getenv("LAB6_CONTAINERS") != "1", reason="Wymaga LAB6_CONTAINERS=1 i Docker Compose")]
ROOT = Path(__file__).resolve().parents[2]


def docker(*args):
    return subprocess.check_output(["docker", *args], cwd=ROOT, text=True, timeout=30)


def test_containers_are_healthy_and_eureka_advertises_container_names():
    services = ("eureka", "redis", "rabbitmq", "inventory", "outbound", "shipping", "auth", "api-gateway")
    for service in services:
        identifier = docker("compose", "ps", "-q", service).strip()
        assert identifier, service
        state = json.loads(docker("inspect", identifier))[0]
        assert state["State"]["Health"]["Status"] == "healthy", service
        if service in ("inventory", "outbound", "shipping", "auth", "api-gateway", "eureka"):
            assert state["Config"]["User"] not in ("", "root", "0"), service
    code = ("import urllib.request; print(urllib.request.urlopen(urllib.request.Request("
            "'http://eureka:8761/eureka/apps',headers={'Accept':'application/json'})).read().decode())")
    registry = json.loads(docker("compose", "exec", "-T", "inventory", "python", "-c", code))
    apps = {app["name"]: app for app in registry["applications"]["application"]}
    for service in ("inventory", "outbound", "shipping", "auth"):
        instances = apps[f"{service.upper()}-SERVICE"]["instance"]
        assert any(i["hostName"] == service and i["status"] == "UP" for i in instances)


def test_only_gateway_has_published_ports(gateway_client):
    identifiers = docker("compose", "ps", "-q").split()
    containers = json.loads(docker("inspect", *identifiers))
    published = []
    for container in containers:
        service = container["Config"]["Labels"]["com.docker.compose.service"]
        for port, bindings in (container["NetworkSettings"]["Ports"] or {}).items():
            for binding in bindings or []:
                published.append((service, port, binding["HostIp"], binding["HostPort"]))
    assert published == [("api-gateway", "8080/tcp", "127.0.0.1", "8080")]
    # Inne projekty mogą mieć własny Redis na hoście. Mapowania wszystkich
    # kontenerów tego projektu sprawdzamy powyżej; tu porty domenowe LAB6.
    for port in (8101, 8102, 8103, 8104):
        with socket.socket() as client:
            client.settimeout(1)
            assert client.connect_ex(("127.0.0.1", port)) != 0, f"Port hosta {port} jest zajęty"
    response = gateway_client.get("/api/stocks")
    assert response.status_code == 200
    assert response.headers["x-gateway"] == "lab4"


def test_private_signing_key_is_mounted_only_in_auth():
    for service in ("inventory", "outbound", "shipping", "auth"):
        identifier = docker("compose", "ps", "-q", service).strip()
        container = json.loads(docker("inspect", identifier))[0]
        mounts = {mount["Destination"]: mount for mount in container["Mounts"]}
        assert mounts["/data"]["Type"] == "volume"
        assert mounts["/keys/public"]["RW"] is False
        if service == "auth":
            assert mounts["/keys/private"]["RW"] is False
        else:
            assert "/keys/private" not in mounts
            assert docker("compose", "exec", "-T", service, "python", "-c",
                          "from pathlib import Path; print(Path('/keys/private/jwt-private.pem').exists())").strip() == "False"
