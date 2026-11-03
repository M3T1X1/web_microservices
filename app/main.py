import os
from contextlib import ExitStack, asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.cache import JsonCache, make_redis
from app.database import Base, make_database
from app.db_models import OrderRow, StockRow
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
        if not prefix.strip():
            raise ValueError("CACHE_PREFIX must not be empty")
        with ExitStack() as resources:
            engine, sessions = make_database(
                database_url or os.getenv("DATABASE_URL", "sqlite:///./inventory.db"))
            resources.callback(engine.dispose)
            outbound_engine, outbound_sessions = make_database(
                outbound_database_url or os.getenv("OUTBOUND_DATABASE_URL", "sqlite:///./outbound.db"))
            resources.callback(outbound_engine.dispose)
            client = redis_client if redis_client is not None else make_redis(
                os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"))
            resources.callback(client.close)
            # Base pochodzi z ZIP. Każda baza otrzymuje wyłącznie tabelę swojej domeny.
            Base.metadata.create_all(engine, tables=[StockRow.__table__])
            Base.metadata.create_all(outbound_engine, tables=[OrderRow.__table__])
            app.state.engine = engine
            app.state.outbound_engine = outbound_engine
            app.state.redis = client
            app.state.stock_repository = StockRepository(sessions)
            app.state.order_repository = OrderRepository(outbound_sessions)
            app.state.stock_cache = JsonCache(client, prefix=f"{prefix}:inventory", ttl=ttl, resource_name="stock")
            app.state.order_cache = JsonCache(client, prefix=f"{prefix}:outbound", ttl=ttl, resource_name="order")
            app.state.stock_service = StockService(app.state.stock_repository, app.state.stock_cache)
            app.state.order_service = OrderService(app.state.order_repository, app.state.order_cache)
            app.state.stock_repository.seed()
            yield

    app = FastAPI(title="Magazyn — Inventory i Outbound", version="0.2.0",
                  description="LAB2: SQLite i Redis na bazie oficjalnego kodu pomocniczego", lifespan=lifespan)
    app.include_router(health.router)
    app.include_router(stocks.router)
    app.include_router(orders.router)

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, exc: SQLAlchemyError):
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
        status = "error" if not db_ok else "ok" if cache_ok else "degraded"
        return JSONResponse(status_code=200 if db_ok else 503, content={
            "service": "warehouse", "status": status,
            "database": databases, "cache": "ok" if cache_ok else "down",
        })

    return app


app = create_app()
