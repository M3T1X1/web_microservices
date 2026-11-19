"""Shipping :8103 — własna baza przesyłek i komunikacja z Outbound przez Eurekę."""
from app.main import create_app


def create_shipping_app(**kwargs):
    return create_app(domain="shipping", discovery_enabled=True, **kwargs)


app = create_shipping_app()
