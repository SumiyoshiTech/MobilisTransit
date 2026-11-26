"""SQLAlchemy 2.0 declarative base, naming conventions, and cross-engine UTC datetime type."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, MetaData
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import TypeDecorator

NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utc_now() -> datetime:
    """Return the current timezone-aware UTC timestamp."""
    return datetime.now(UTC)


def normalize_utc(value: datetime) -> datetime:
    """Convert any naive or aware datetime into a timezone-aware UTC datetime."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """Cross-engine UTC datetime storing naive UTC on SQLite and tz-aware UTC on PostgreSQL."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> Any:
        if value is None:
            return None
        utc_dt = normalize_utc(value)
        if dialect.name == "sqlite":
            return utc_dt.replace(tzinfo=None)
        return utc_dt

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return normalize_utc(value)


class Base(DeclarativeBase):
    """Declarative base shared by all MobilisTransit relational ORM models."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
