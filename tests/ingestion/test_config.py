import os

from app.ingestion.config import IngestionSettings, get_settings


def test_default_settings():
    settings = IngestionSettings()
    assert settings.chunk_size == 1500
    assert settings.chunk_overlap == 200
    assert settings.ocr_text_threshold == 20


def test_settings_overridable_via_env(monkeypatch):
    monkeypatch.setenv("INGESTION_CHUNK_SIZE", "500")
    settings = IngestionSettings()
    assert settings.chunk_size == 500


def test_get_settings_returns_cached_instance():
    assert get_settings() is get_settings()
