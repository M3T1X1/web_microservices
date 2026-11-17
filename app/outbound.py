import os
from contextlib import ExitStack, asynccontextmanager

from fastapi import FastAPI

from app.application import create_service_app
from app.cache import JsonCache, make_redis
from app.database import Base, make_database
from app.db_models import OrderRow
from app.eureka import create_eureka_client
from app.repositories import OrderRepository
from app.routers import orders
from app.services.orders import OrderService


def create_app(database_url=None, redis_client=None, *, cache_prefix=None,
               cache_ttl=None, eureka_client=None) -> FastAPI:
    registry_client = eureka_client if eureka_client is not None else create_eureka_client("outbound", 8102)

    @asynccontextmanager
    async def storage_lifespan(app):
        prefix = cache_prefix if cache_prefix is not None else os.getenv("CACHE_PREFIX", "lab2:local:warehouse")
        ttl = int(cache_ttl if cache_ttl is not None else os.getenv("CACHE_TTL_SECONDS", "60"))
        if not prefix.strip():
            raise ValueError("CACHE_PREFIX must not be empty")
        with ExitStack() as resources:
            engine, sessions = make_database(
                database_url or os.getenv("OUTBOUND_DATABASE_URL", "sqlite:///./outbound.db"))
            resources.callback(engine.dispose)
            client = redis_client if redis_client is not None else make_redis(
                os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"))
            resources.callback(client.close)
            Base.metadata.create_all(engine, tables=[OrderRow.__table__])
            app.state.engine = engine
            app.state.redis = client
            app.state.order_repository = OrderRepository(sessions)
            app.state.order_cache = JsonCache(client, prefix=f"{prefix}:outbound", ttl=ttl, resource_name="order")
            app.state.order_service = OrderService(app.state.order_repository, app.state.order_cache)
            yield

    app = create_service_app("outbound", "Zamówienia i pakowanie. SQLite i Redis.",
                             storage_lifespan, registry_client)
    app.include_router(orders.router)
    return app


app = create_app()
