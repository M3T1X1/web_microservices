"""Inventory jako osobny proces: stany i przyjęcia, wyłącznie własna baza."""
from app.main import create_app


def create_inventory_app(**kwargs):
    return create_app(domain="inventory", discovery_enabled=True, **kwargs)


app = create_inventory_app()
