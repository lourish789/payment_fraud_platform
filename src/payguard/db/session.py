from __future__ import annotations

import contextlib
import threading

from sqlalchemy import create_engine, event, inspect, text
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


class FairLock:
    """FIFO mutex. threading.Lock makes no ordering promise, so background loops that release and
    immediately re-acquire it (outbox relay, agent workers) can starve a request thread indefinitely;
    measured: an analyst's request waited >60 s while the relay drained a 93k-event backlog. Tickets
    bound a request's wait to the writers already queued ahead of it."""

    def __init__(self):
        self._cv = threading.Condition(threading.Lock())
        self._next = self._serving = 0

    def __enter__(self):
        with self._cv:
            ticket = self._next
            self._next += 1
            while ticket != self._serving:
                self._cv.wait()

    def __exit__(self, *exc):
        with self._cv:
            self._serving += 1
            self._cv.notify_all()


_sqlite_write_locks: dict[int, FairLock] = {}


def write_guard(session_factory: sessionmaker):
    """Serialise writers in-process when the database is SQLite.

    SQLite allows one writer at a time and resolves contention by sleeping and retrying (backoff steps up
    to 100 ms), which under 16 concurrent request threads made the persist stage average 168 ms. Queuing
    writers on a lock hands off immediately instead. Postgres has row-level locking, so this is a no-op
    there."""
    engine = session_factory.kw["bind"]
    if engine.dialect.name != "sqlite":
        return contextlib.nullcontext()
    return _sqlite_write_locks.setdefault(id(engine), FairLock())


def make_session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(bind=engine, expire_on_commit=False)


# Additive columns introduced after the first release: (table, column, SQL type and default).
_ADDED_COLUMNS = [
    ("transactions", "rail", "VARCHAR(20) DEFAULT 'card'"),
    ("transactions", "counterparty_key", "VARCHAR(32)"),
    ("decisions", "actions", "JSON"),
    ("api_clients", "key_prefix", "VARCHAR(12)"),
    ("api_clients", "revoked_at", "TIMESTAMP"),
]


def init_db(engine: Engine) -> None:
    # Dev convenience: create_all plus additive column migrations. A production deploy would run
    # Alembic migrations instead.
    Base.metadata.create_all(engine)
    insp = inspect(engine)
    with engine.begin() as conn:
        for table, column, ddl in _ADDED_COLUMNS:
            if column not in {c["name"] for c in insp.get_columns(table)}:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
        for table in Base.metadata.sorted_tables:  # indexes added after a table was first created
            for index in table.indexes:
                index.create(conn, checkfirst=True)
        if engine.dialect.name == "sqlite":
            # Without statistics SQLite picks the unique index over the covering one for joins.
            analyzed = conn.execute(text("SELECT 1 FROM sqlite_master WHERE name = 'sqlite_stat1'")).first()
            conn.execute(text("PRAGMA optimize" if analyzed else "ANALYZE"))
