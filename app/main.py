import os
from contextlib import ExitStack, asynccontextmanager

from fastapi import FastAPI

from app.application import create_service_app
from app.cache import JsonCache, make_redis
from app.database import Base, make_database
from app.db_models import ReceiptRow, StockRow
from app.eureka import create_eureka_client
from app.repositories import ReceiptRepository, StockRepository
from app.routers import receipts, stocks
from app.services.receipts import ReceiptService
from app.services.stocks import StockService


def create_app(database_url=None, redis_client=None, *, cache_prefix=None,
               cache_ttl=None, eureka_client=None) -> FastAPI:
    registry_client = eureka_client if eureka_client is not None else create_eureka_client("inventory", 8101)

    @asynccontextmanager
    async def storage_lifespan(app):
        prefix = cache_prefix if cache_prefix is not None else os.getenv("CACHE_PREFIX", "lab2:local:warehouse")
        ttl = int(cache_ttl if cache_ttl is not None else os.getenv("CACHE_TTL_SECONDS", "60"))
        if not prefix.strip():
            raise ValueError("CACHE_PREFIX must not be empty")
        with ExitStack() as resources:
            engine, sessions = make_database(
                database_url or os.getenv("DATABASE_URL", "sqlite:///./inventory.db"))
            resources.callback(engine.dispose)
            client = redis_client if redis_client is not None else make_redis(
                os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"))
            resources.callback(client.close)
            Base.metadata.create_all(engine, tables=[StockRow.__table__, ReceiptRow.__table__])
            app.state.engine = engine
            app.state.redis = client
            app.state.stock_repository = StockRepository(sessions)
            app.state.receipt_repository = ReceiptRepository(sessions)
            app.state.stock_cache = JsonCache(client, prefix=f"{prefix}:inventory", ttl=ttl, resource_name="stock")
            app.state.stock_service = StockService(app.state.stock_repository, app.state.stock_cache)
            app.state.receipt_service = ReceiptService(app.state.receipt_repository, app.state.stock_cache)
            app.state.stock_repository.seed()
            yield

    app = create_service_app("inventory", "Stany, rezerwacje i przyjęcia. SQLite i Redis.",
                             storage_lifespan, registry_client)
    app.include_router(stocks.router)
    app.include_router(receipts.router)

    return app


app = create_app()
