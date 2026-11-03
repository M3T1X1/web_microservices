import os
from contextlib import ExitStack, asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.cache import JsonCache, make_redis
from app.database import make_database
from app.db_models import InventoryBase, OutboundBase
from app.repositories import OrderRepository, StockRepository
from app.routers import health, orders, stocks
from app.services.orders import OrderService
from app.services.stocks import StockService


def create_app(database_url=None, redis_client=None, *, outbound_database_url=None,
               cache_prefix=None, cache_ttl=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        prefix = cache_prefix if cache_prefix is not None else os.getenv("CACHE_PREFIX", "lab2:local:warehouse")
        ttl = int(cache_ttl if cache_ttl is not None else os.getenv("CACHE_TTL_SECONDS", "60"))
        with ExitStack() as resources:
            inventory_engine, inventory_sessions = make_database(
                database_url or os.getenv("DATABASE_URL", "sqlite:///./inventory.db"))
            resources.callback(inventory_engine.dispose)
            outbound_engine, outbound_sessions = make_database(
                outbound_database_url or os.getenv("OUTBOUND_DATABASE_URL", "sqlite:///./outbound.db"))
            resources.callback(outbound_engine.dispose)
            client = redis_client if redis_client is not None else make_redis(
                os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"))
            resources.callback(client.close)
            InventoryBase.metadata.create_all(inventory_engine)
            OutboundBase.metadata.create_all(outbound_engine)
            app.state.engine = inventory_engine
            app.state.outbound_engine = outbound_engine
            app.state.redis = client
            app.state.stock_repository = StockRepository(inventory_sessions)
            app.state.order_repository = OrderRepository(outbound_sessions)
            app.state.stock_cache = JsonCache(client, prefix=f"{prefix}:inventory", resource="stock", ttl=ttl)
            app.state.order_cache = JsonCache(client, prefix=f"{prefix}:outbound", resource="order", ttl=ttl)
            app.state.stock_service = StockService(app.state.stock_repository, app.state.stock_cache)
            app.state.order_service = OrderService(app.state.order_repository, app.state.order_cache)
            app.state.stock_repository.seed()
            yield

    app = FastAPI(title="Magazyn — Inventory i Outbound", version="0.2.0",
                  description="LAB2: trwałe dane SQLite i opcjonalny cache Redis", lifespan=lifespan)
    app.include_router(health.router)
    app.include_router(stocks.router)
    app.include_router(orders.router)

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, exc: SQLAlchemyError):
        # Nie ujawniamy SQL ani danych odbiorcy. Błąd bazy nie jest konfliktem 409.
        return JSONResponse(status_code=503, content={"detail": "Database unavailable"})

    @app.get("/ready")
    def readiness(request: Request):
        databases = {}
        for name, engine in (("inventory", request.app.state.engine),
                             ("outbound", request.app.state.outbound_engine)):
            try:
                with engine.connect() as connection:
                    connection.execute(text("SELECT 1"))
                databases[name] = "ok"
            except SQLAlchemyError:
                databases[name] = "down"
        try:
            cache_ok = bool(request.app.state.redis.ping())
        except RedisError:
            cache_ok = False
        db_ok = all(value == "ok" for value in databases.values())
        return JSONResponse(status_code=200 if db_ok else 503, content={
            "service": "warehouse", "status": "error" if not db_ok else "ok" if cache_ok else "degraded",
            "database": databases, "cache": "ok" if cache_ok else "down",
        })

    return app


app = create_app()
