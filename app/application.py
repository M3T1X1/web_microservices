from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.routers import health


def create_service_app(service_name, description, storage_lifespan, eureka_client) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        async with storage_lifespan(app):
            app.state.eureka_client = eureka_client
            try:
                await eureka_client.start()
                yield
            finally:
                await eureka_client.stop()

    app = FastAPI(title=f"Magazyn — {service_name.title()}", version="0.4.0",
                  description=description, lifespan=lifespan)
    app.state.service_name = service_name
    app.include_router(health.router)

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, exc: SQLAlchemyError):
        return JSONResponse(status_code=503, content={"detail": "Database unavailable"})

    @app.get("/ready")
    def readiness(request: Request):
        try:
            with request.app.state.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            database_ok = True
        except SQLAlchemyError:
            database_ok = False
        try:
            cache_ok = bool(request.app.state.redis.ping())
        except RedisError:
            cache_ok = False
        status = "error" if not database_ok else "ok" if cache_ok else "degraded"
        return JSONResponse(status_code=200 if database_ok else 503, content={
            "service": service_name, "status": status,
            "database": {service_name: "ok" if database_ok else "down"},
            "cache": "ok" if cache_ok else "down",
        })

    return app

