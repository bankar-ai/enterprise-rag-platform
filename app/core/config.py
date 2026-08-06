"""Database connection settings, loaded from environment variables."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    """Postgres connection settings. Overridable via the `DATABASE_URL` env var."""

    model_config = SettingsConfigDict(env_prefix="")

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/erp"


@lru_cache
def get_database_settings() -> DatabaseSettings:
    """Return the process-wide cached `DatabaseSettings` instance."""
    return DatabaseSettings()
