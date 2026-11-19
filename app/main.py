import os
from contextlib import ExitStack, asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.cache import JsonCache, make_redis
from app.database import Base, make_database
from app.db_models import OrderRow, ReceiptRow, StockRow
from app.discovery import create_eureka_client
from app.repositories import OrderRepository, ReceiptRepository, StockRepository
from app.routers import health, orders, receipts, stocks
from app.services.orders import OrderService
from app.services.receipts import ReceiptService
from app.services.stocks import StockService


def create_app(database_url=None, redis_client=None, *, outbound_database_url=None,
               cache_prefix=None, cache_ttl=None, domain="both",
               discovery_enabled=False, eureka_client=None) -> FastAPI:
    if domain not in {"both", "inventory", "outbound"}:
        raise ValueError("Unknown domain")
    inventory_enabled = domain in {"both", "inventory"}
    outbound_enabled = domain in {"both", "outbound"}

    @asynccontextmanager
    async def lifespan(app):
        prefix = cache_prefix if cache_prefix is not None else os.getenv("CACHE_PREFIX", "lab2:local:warehouse")
        ttl = int(cache_ttl if cache_ttl is not None else os.getenv("CACHE_TTL_SECONDS", "60"))
        if not prefix.strip():
            raise ValueError("CACHE_PREFIX must not be empty")
        with ExitStack() as resources:
            client = redis_client if redis_client is not None else make_redis(
                os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"))
            resources.callback(client.close)
            app.state.redis = client
            app.state.databases = {}
            if inventory_enabled:
                engine, sessions = make_database(
                    database_url or os.getenv("DATABASE_URL", "sqlite:///./inventory.db"))
                resources.callback(engine.dispose)
                Base.metadata.create_all(engine, tables=[StockRow.__table__, ReceiptRow.__table__])
                app.state.engine = engine
                app.state.databases["inventory"] = engine
                app.state.stock_repository = StockRepository(sessions)
                app.state.receipt_repository = ReceiptRepository(sessions)
                app.state.stock_cache = JsonCache(client, prefix=f"{prefix}:inventory", ttl=ttl, resource_name="stock")
                app.state.stock_service = StockService(app.state.stock_repository, app.state.stock_cache)
                app.state.receipt_service = ReceiptService(app.state.receipt_repository, app.state.stock_cache)
                app.state.stock_repository.seed()
            if outbound_enabled:
                engine, sessions = make_database(
                    outbound_database_url or os.getenv("OUTBOUND_DATABASE_URL", "sqlite:///./outbound.db"))
                resources.callback(engine.dispose)
                Base.metadata.create_all(engine, tables=[OrderRow.__table__])
                app.state.outbound_engine = engine
                app.state.databases["outbound"] = engine
                app.state.order_repository = OrderRepository(sessions)
                app.state.order_cache = JsonCache(client, prefix=f"{prefix}:outbound", ttl=ttl, resource_name="order")
                app.state.order_service = OrderService(app.state.order_repository, app.state.order_cache)
            app.state.eureka_client = eureka_client
            if discovery_enabled:
                discovery = eureka_client if eureka_client is not None else create_eureka_client(domain)
                app.state.eureka_client = discovery
                try:
                    await discovery.start()
                    yield
                finally:
                    await discovery.stop()
                return
            yield

    app = FastAPI(title=f"Magazyn — {domain}", version="0.4.0",
                  description="Inventory: stany i przyjęcia; Outbound: zamówienia. SQLite i Redis.", lifespan=lifespan)
    if domain == "both":
        app.include_router(health.router)
    if inventory_enabled:
        app.include_router(stocks.router)
        app.include_router(receipts.router)
    if outbound_enabled:
        app.include_router(orders.router)
    if domain != "both":
        @app.get("/health")
        def service_health():
            return {"status": "ok", "service": f"{domain.upper()}-SERVICE"}

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, exc: SQLAlchemyError):
        return JSONResponse(status_code=503, content={"detail": "Database unavailable"})

    @app.get("/ready")
    def readiness(request: Request):
        databases = {}
        for name, engine in request.app.state.databases.items():
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
