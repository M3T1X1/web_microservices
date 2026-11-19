"""Outbound jako osobny proces: zamówienia i odczyt Inventory przez Eurekę."""
from app.main import create_app


def create_outbound_app(**kwargs):
    return create_app(domain="outbound", discovery_enabled=True, **kwargs)


app = create_outbound_app()
