"""Application settings loaded from environment variables and optional `.env` file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

LlmProvider = Literal["ollama", "openai_compatible"]


def ensure_sqlite_parent_dir(database_url: str) -> Path | None:
    """Create the parent directory for file-backed SQLite URLs if it does not exist.

    Returns the resolved parent directory for file-backed SQLite databases, or ``None``
    for non-SQLite URLs and in-memory SQLite databases.
    """
    url = make_url(database_url)
    if not url.drivername.startswith("sqlite"):
        return None

    db_path = url.database
    if not db_path or db_path == ":memory:":
        return None
    if url.query.get("mode") == "memory":
        return None

    cleaned_path = db_path.removeprefix("file:")
    if not cleaned_path or cleaned_path == ":memory:":
        return None

    parent_dir = Path(cleaned_path).parent
    parent_dir.mkdir(parents=True, exist_ok=True)
    return parent_dir.resolve()


class Settings(BaseSettings):
    """Runtime configuration for the MobilisTransit central FastAPI server."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(
        default="sqlite:///./data/mobilistransit.db",
        description="SQLAlchemy database URL (SQLite WAL default, PostgreSQL-ready).",
    )
    sumo_network_dir: Path | None = Field(
        default=None,
        description="Optional explicit SUMO network directory override.",
    )
    active_model_pointer: Path = Field(
        default=Path("ml/artifacts/active_model.json"),
        description="Path to the active versioned AAP model pointer JSON file.",
    )
    stop_hold_min_s: int = Field(default=0, ge=0)
    stop_hold_max_s: int = Field(default=900, ge=0)
    traffic_multiplier_min: float = Field(default=0.5, gt=0.0)
    traffic_multiplier_max: float = Field(default=2.5, gt=0.0)
    max_concurrent_sumo_jobs: int = Field(default=1, ge=1)
    sumo_run_timeout_s: int = Field(default=180, ge=1)
    llm_provider: LlmProvider = "ollama"
    llm_model: str = "qwen2.5:3b"
    ollama_base_url: str = "http://localhost:11434"
    openai_api_base: str | None = None
    openai_api_key: str | None = Field(default=None, repr=False)

    @field_validator("sumo_network_dir", "openai_api_base", "openai_api_key", mode="before")
    @classmethod
    def _empty_str_to_none(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("database_url", "llm_model", "ollama_base_url")
    @classmethod
    def _reject_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Setting must not be blank.")
        return stripped

    @model_validator(mode="after")
    def _validate_bounds_and_prepare_storage(self) -> Settings:
        if self.stop_hold_min_s > self.stop_hold_max_s:
            raise ValueError(
                f"stop_hold_min_s ({self.stop_hold_min_s}) must be <= "
                f"stop_hold_max_s ({self.stop_hold_max_s})."
            )
        if self.traffic_multiplier_min > self.traffic_multiplier_max:
            raise ValueError(
                f"traffic_multiplier_min ({self.traffic_multiplier_min}) must be <= "
                f"traffic_multiplier_max ({self.traffic_multiplier_max})."
            )
        ensure_sqlite_parent_dir(self.database_url)
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached process-wide `Settings` instance."""
    return Settings()
