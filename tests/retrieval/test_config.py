from app.retrieval.config import RetrievalSettings, get_retrieval_settings


def test_retrieval_settings_defaults():
    settings = RetrievalSettings()
    assert settings.redis_url == "redis://localhost:6379/0"
    assert settings.cache_ttl_seconds == 300


def test_retrieval_settings_env_override(monkeypatch):
    monkeypatch.setenv("RETRIEVAL_REDIS_URL", "redis://example:6380/2")
    monkeypatch.setenv("RETRIEVAL_CACHE_TTL_SECONDS", "60")
    settings = RetrievalSettings()
    assert settings.redis_url == "redis://example:6380/2"
    assert settings.cache_ttl_seconds == 60


def test_get_retrieval_settings_is_cached():
    assert get_retrieval_settings() is get_retrieval_settings()
