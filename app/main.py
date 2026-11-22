import os
import logging
from contextlib import ExitStack, asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool

from app.cache import JsonCache, make_redis
from app.database import Base, make_database
from app.db_models import OrderDispatchRow, OrderRow, ProcessedEventRow, ReceiptRow, ReservationBatchRow, ShipmentRow, StockRow
from app.messaging import BrokerUnavailableError, RabbitEventBroker
from app.models.events import OUTBOUND_SHIPMENT_QUEUE, SHIPMENT_ROUTING_KEY
from app.discovery import create_eureka_client
from app.repositories import OrderRepository, ReceiptRepository, StockRepository
from app.routers import allocations, health, orders, receipts, shipments, stocks
from app.security import TokenVerifier, require_roles
from app.services.allocation import AllocationService
from app.services.orders import OrderService
from app.services.receipts import ReceiptService
from app.services.shipping import ShippingService
from app.services.stocks import StockService


def create_app(database_url=None, redis_client=None, *, outbound_database_url=None,
               cache_prefix=None, cache_ttl=None, domain="both",
               discovery_enabled=False, eureka_client=None, shipping_database_url=None,
               auth_enabled=None, token_verifier=None, event_broker=None) -> FastAPI:
    if domain not in {"both", "inventory", "outbound", "shipping"}:
        raise ValueError("Unknown domain")
    inventory_enabled = domain in {"both", "inventory"}
    outbound_enabled = domain in {"both", "outbound"}

    @asynccontextmanager
    async def lifespan(app):
        enabled = auth_enabled if auth_enabled is not None else True
        app.state.auth_enabled = enabled
        app.state.token_verifier = token_verifier or (TokenVerifier.from_env() if enabled else None)
        prefix = cache_prefix if cache_prefix is not None else os.getenv("CACHE_PREFIX", "lab2:local:warehouse")
        ttl = int(cache_ttl if cache_ttl is not None else os.getenv("CACHE_TTL_SECONDS", "60"))
        if not prefix.strip():
            raise ValueError("CACHE_PREFIX must not be empty")
        with ExitStack() as resources:
            broker = event_broker if event_broker is not None else RabbitEventBroker()
            resources.callback(broker.close)
            app.state.event_broker = broker
            client = redis_client if redis_client is not None else make_redis(
                os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"))
            resources.callback(client.close)
            app.state.redis = client
            app.state.databases = {}
            if inventory_enabled:
                engine, sessions = make_database(
                    database_url or os.getenv("DATABASE_URL", "sqlite:///./inventory.db"))
                resources.callback(engine.dispose)
                Base.metadata.create_all(engine, tables=[StockRow.__table__, ReceiptRow.__table__, ReservationBatchRow.__table__])
                app.state.engine = engine
                app.state.databases["inventory"] = engine
                app.state.stock_repository = StockRepository(sessions)
                app.state.receipt_repository = ReceiptRepository(sessions)
                app.state.stock_cache = JsonCache(client, prefix=f"{prefix}:inventory", ttl=ttl, resource_name="stock")
                app.state.stock_service = StockService(app.state.stock_repository, app.state.stock_cache)
                app.state.receipt_service = ReceiptService(app.state.receipt_repository, app.state.stock_cache)
                app.state.allocation_service = AllocationService(sessions, app.state.stock_cache)
                app.state.stock_repository.seed()
            if outbound_enabled:
                engine, sessions = make_database(
                    outbound_database_url or os.getenv("OUTBOUND_DATABASE_URL", "sqlite:///./outbound.db"))
                resources.callback(engine.dispose)
                Base.metadata.create_all(engine, tables=[OrderRow.__table__, OrderDispatchRow.__table__, ProcessedEventRow.__table__])
                app.state.outbound_engine = engine
                app.state.databases["outbound"] = engine
                app.state.order_repository = OrderRepository(sessions)
                app.state.order_cache = JsonCache(client, prefix=f"{prefix}:outbound", ttl=ttl, resource_name="order")
                app.state.order_service = OrderService(app.state.order_repository, app.state.order_cache, broker)
                try:
                    await run_in_threadpool(broker.bind_queue, OUTBOUND_SHIPMENT_QUEUE, SHIPMENT_ROUTING_KEY)
                except BrokerUnavailableError:
                    logging.getLogger(__name__).warning("RabbitMQ unavailable; Outbound HTTP remains available")
            if domain == "shipping":
                engine, sessions = make_database(shipping_database_url or os.getenv("SHIPPING_DATABASE_URL", "sqlite:///./shipping.db"))
                resources.callback(engine.dispose)
                Base.metadata.create_all(engine, tables=[ShipmentRow.__table__])
                app.state.databases["shipping"] = engine
                app.state.shipping_service = ShippingService(sessions, eureka_client, broker)
            app.state.eureka_client = eureka_client
            if discovery_enabled:
                discovery = eureka_client if eureka_client is not None else create_eureka_client(domain)
                app.state.eureka_client = discovery
                if domain == "shipping":
                    app.state.shipping_service.discovery = discovery
                try:
                    await discovery.start()
                    yield
                finally:
                    await discovery.stop()
                return
            yield

    app = FastAPI(title=f"Magazyn — {domain}", version="0.5.0",
                  description="System magazynowy: dane własnej domeny w SQLite, JWT i Eureka.", lifespan=lifespan)
    if domain == "both":
        app.include_router(health.router)
    if inventory_enabled:
        app.include_router(stocks.router, dependencies=[Depends(require_roles("WAREHOUSE"))])
        app.include_router(allocations.router)
        app.include_router(receipts.router, dependencies=[Depends(require_roles("WAREHOUSE"))])
    if outbound_enabled:
        app.include_router(orders.router)
    if domain == "shipping":
        app.include_router(shipments.router)
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
