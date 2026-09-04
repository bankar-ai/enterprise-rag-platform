from app.retrieval.config import RetrievalSettings, get_retrieval_settings


def test_retrieval_settings_defaults():
    settings = RetrievalSettings()
    assert settings.redis_url.startswith("redis://")
    assert settings.cache_ttl_seconds == 300
    assert settings.redis_socket_timeout_seconds == 2.0


def test_retrieval_settings_env_override(monkeypatch):
    monkeypatch.setenv("RETRIEVAL_REDIS_URL", "redis://example:6380/2")
    monkeypatch.setenv("RETRIEVAL_CACHE_TTL_SECONDS", "60")
    monkeypatch.setenv("RETRIEVAL_REDIS_SOCKET_TIMEOUT_SECONDS", "0.5")
    settings = RetrievalSettings()
    assert settings.redis_url == "redis://example:6380/2"
    assert settings.cache_ttl_seconds == 60
    assert settings.redis_socket_timeout_seconds == 0.5


def test_get_retrieval_settings_is_cached():
    assert get_retrieval_settings() is get_retrieval_settings()
