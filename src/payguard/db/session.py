from __future__ import annotations

import contextlib
import threading

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from payguard.db.models import Base


def make_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})

        @event.listens_for(engine, "connect")
        def _pragmas(conn, _):  # WAL lets readers proceed while the scorer writes
            cur = conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

        return engine
    return create_engine(url, pool_size=20, max_overflow=20, pool_pre_ping=True)


_sqlite_write_locks: dict[int, threading.Lock] = {}


def write_guard(session_factory: sessionmaker):
    """Serialise writers in-process when the database is SQLite.

    SQLite allows one writer at a time and resolves contention by sleeping and retrying (backoff steps up
    to 100 ms), which under 16 concurrent request threads made the persist stage average 168 ms. Queuing
    writers on a lock hands off immediately instead. Postgres has row-level locking, so this is a no-op
    there."""
    engine = session_factory.kw["bind"]
    if engine.dialect.name != "sqlite":
        return contextlib.nullcontext()
    return _sqlite_write_locks.setdefault(id(engine), threading.Lock())


def make_session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(bind=engine, expire_on_commit=False)


def init_db(engine: Engine) -> None:
    # Dev convenience. A production deploy would run Alembic migrations instead of create_all.
    Base.metadata.create_all(engine)
