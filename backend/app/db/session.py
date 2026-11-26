"""SQLAlchemy 2.0 engine, session factory, and SQLite WAL/FK initialization helpers."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.db.base import Base
from backend.app.settings import ensure_sqlite_parent_dir


def _is_in_memory_sqlite(database_url: str) -> bool:
    url = make_url(database_url)
    if not url.drivername.startswith("sqlite"):
        return False
    db_path = url.database
    return not db_path or db_path == ":memory:" or url.query.get("mode") == "memory"


def create_db_engine(database_url: str, *, echo: bool = False) -> Engine:
    """Create a SQLAlchemy 2.0 engine configured for SQLite WAL or PostgreSQL.

    When ``database_url`` targets SQLite:
    - Creates parent directories automatically for file-backed databases.
    - Enables ``PRAGMA foreign_keys=ON``, ``PRAGMA busy_timeout=5000``, and
      (for file-backed databases) ``PRAGMA journal_mode=WAL`` on every connection.
    """
    url = make_url(database_url)
    is_sqlite = url.drivername.startswith("sqlite")

    if is_sqlite:
        in_memory = _is_in_memory_sqlite(database_url)
        if not in_memory:
            ensure_sqlite_parent_dir(database_url)

        engine_kwargs: dict[str, Any] = {
            "echo": echo,
            "connect_args": {"check_same_thread": False, "timeout": 5.0},
        }
        if in_memory:
            engine_kwargs["poolclass"] = StaticPool

        engine = create_engine(database_url, **engine_kwargs)

        @event.listens_for(engine, "connect")
        def _configure_sqlite_connection(dbapi_connection: Any, _connection_record: Any) -> None:
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA busy_timeout=5000")
                if not in_memory:
                    cursor.execute("PRAGMA journal_mode=WAL")
            finally:
                cursor.close()

        return engine

    return create_engine(database_url, echo=echo, pool_pre_ping=True)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Build a SQLAlchemy 2.0 `sessionmaker` bound to `engine`."""
    return sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )


def init_db(engine: Engine) -> None:
    """Import all ORM models and create missing database tables and indexes."""
    import backend.app.db.models  # noqa: F401

    Base.metadata.create_all(bind=engine)


@contextmanager
def session_scope(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    """Provide a transactional SQLAlchemy session scope with automatic rollback on error."""
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
