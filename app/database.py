"""Silnik współdzielony w aplikacji, osobna sesja dla każdej operacji."""
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


def make_database(url: str) -> tuple[Engine, sessionmaker[Session]]:
    options = {}
    if url.startswith("sqlite:"):
        options["connect_args"] = {"check_same_thread": False, "timeout": 5}
        if url in {"sqlite://", "sqlite:///:memory:"}:
            options["poolclass"] = StaticPool
    engine = create_engine(url, **options)
    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def configure_sqlite(connection, _):
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
    return engine, sessionmaker(engine, expire_on_commit=False)


@contextmanager
def write_transaction(sessions: sessionmaker[Session]):
    # begin(): commit po sukcesie, rollback po wyjątku, zawsze zamknięcie sesji.
    with sessions.begin() as db:
        if db.get_bind().dialect.name == "sqlite":
            # Blokada zapisu PRZED odczytem reguły, także między procesami.
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        yield db
