import os

from py_eureka_client.eureka_client import EurekaClient


def create_eureka_client(service_name: str, default_port: int) -> EurekaClient:
    prefix = service_name.upper()
    return EurekaClient(
        eureka_server=os.getenv("EUREKA_SERVER_URL", "http://127.0.0.1:8761"),
        eureka_context="/eureka",
        app_name=os.getenv(f"{prefix}_SERVICE_NAME", f"{prefix}-SERVICE"),
        instance_port=int(os.getenv(f"{prefix}_SERVICE_PORT", str(default_port))),
        instance_ip=os.getenv("INSTANCE_IP", "127.0.0.1"),
        instance_host=os.getenv("INSTANCE_HOST", "127.0.0.1"),
        home_page_url="/docs",
        status_page_url="/ready",
        health_check_url="/health",
        strict_service_error_policy=False,
    )
