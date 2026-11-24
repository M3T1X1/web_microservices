"""Adresy Swagger UI dostępnego przez Gateway w środowisku Compose."""
import os


def documentation_settings(service):
    if os.getenv("SWAGGER_GATEWAY_ENABLED", "").lower() != "true":
        return {}
    prefix = f"/docs/{service}"
    return {
        "docs_url": prefix,
        "openapi_url": f"{prefix}/openapi.json",
        "swagger_ui_oauth2_redirect_url": f"{prefix}/oauth2-redirect",
        "redoc_url": None,
        "servers": [{"url": "/api", "description": "API Gateway"}],
    }
