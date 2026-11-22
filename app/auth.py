"""Auth :8104 — własna baza użytkowników, Argon2id i wydawanie JWT RS256."""
from contextlib import asynccontextmanager
import os
from pathlib import Path

from cryptography.hazmat.primitives.serialization import load_pem_private_key, load_pem_public_key
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.database import Base, make_database
from app.db_models import UserRow
from app.discovery import create_eureka_client
from app.routers.auth import router
from app.security import TokenVerifier
from app.services.auth import AuthService
from app.messaging import RabbitEventBroker


def create_auth_app(*, database_url=None, private_key=None, token_verifier=None,
                    discovery_enabled=True, eureka_client=None, token_ttl=None, event_broker=None):
    @asynccontextmanager
    async def lifespan(app):
        key = private_key or Path(os.getenv("JWT_PRIVATE_KEY_FILE", ".local/jwt-private.pem")).read_bytes()
        signing_key = load_pem_private_key(key, password=None)
        if not isinstance(signing_key, RSAPrivateKey) or signing_key.key_size < 2048:
            raise ValueError("Auth requires an RSA private key of at least 2048 bits")
        verifier = token_verifier or TokenVerifier.from_env()
        if signing_key.public_key().public_numbers() != load_pem_public_key(verifier.public_key).public_numbers():
            raise ValueError("JWT signing and verification keys do not match")
        ttl = int(token_ttl if token_ttl is not None else os.getenv("JWT_TTL_SECONDS", "900"))
        if not 1 <= ttl <= 3600:
            raise ValueError("JWT_TTL_SECONDS must be between 1 and 3600")
        engine, sessions = make_database(database_url or os.getenv("AUTH_DATABASE_URL", "sqlite:///./auth.db"))
        discovery = None
        broker = event_broker if event_broker is not None else RabbitEventBroker()
        try:
            app.state.event_broker = broker
            Base.metadata.create_all(engine, tables=[UserRow.__table__])
            app.state.auth_service = AuthService(sessions)
            app.state.auth_enabled = True
            app.state.private_key = key
            app.state.token_verifier = verifier
            app.state.token_ttl = ttl
            app.state.engine = engine
            if discovery_enabled:
                discovery = eureka_client if eureka_client is not None else create_eureka_client("auth")
                await discovery.start()
            yield
        finally:
            try:
                if discovery is not None:
                    await discovery.stop()
            finally:
                broker.close()
                engine.dispose()

    app = FastAPI(title="Magazyn — Auth", version="0.5.0", lifespan=lifespan)
    app.include_router(router)

    @app.get("/health")
    def health():
        return {"status": "ok", "service": "AUTH-SERVICE"}

    @app.get("/ready")
    def ready():
        with app.state.engine.connect() as db:
            db.execute(text("SELECT 1"))
        return {"status": "ok", "database": {"auth": "ok"}}

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Nie zwracamy wejściowego hasła w detail[].input przy błędzie walidacji.
        errors = [{"type": e["type"], "loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": errors})

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request, exc):
        return JSONResponse(status_code=503, content={"detail": "Database unavailable"})

    return app


app = create_auth_app()
