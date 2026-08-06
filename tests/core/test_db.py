import os

from app.core.config import DatabaseSettings
from app.core.db import get_engine, get_session_factory


def test_database_settings_reads_env_var(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@example.invalid:5432/db")
    settings = DatabaseSettings()
    assert settings.database_url == "postgresql+psycopg://u:p@example.invalid:5432/db"


def test_database_settings_has_a_default():
    assert "postgresql" in DatabaseSettings().database_url


def test_get_engine_and_session_factory_are_cached(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ.get("DATABASE_URL", DatabaseSettings().database_url))
    get_engine.cache_clear()
    get_session_factory.cache_clear()
    assert get_engine() is get_engine()
    assert get_session_factory() is get_session_factory()
