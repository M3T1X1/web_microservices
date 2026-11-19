"""Adapter LAB3: rejestracja, heartbeat i lokalna kopia rejestru Eureki."""
import os

from py_eureka_client.eureka_client import EurekaClient


def create_eureka_client(domain: str) -> EurekaClient:
    port = 8101 if domain == "inventory" else 8102
    return EurekaClient(
        eureka_server=os.getenv("EUREKA_SERVER_URL", "http://127.0.0.1:8761"),
        eureka_context="/eureka",
        app_name=f"{domain.upper()}-SERVICE",
        instance_ip="127.0.0.1",
        instance_host="localhost",
        instance_port=int(os.getenv("SERVICE_PORT", str(port))),
        should_discover=domain == "outbound",
        strict_service_error_policy=False,
    )
