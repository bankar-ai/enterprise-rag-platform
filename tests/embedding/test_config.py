from app.embedding.config import EmbeddingSettings


def test_defaults():
    settings = EmbeddingSettings()
    assert settings.model == "nomic-embed-text"
    assert settings.dimension == 768
    assert settings.ollama_host.startswith("http")
    assert settings.faiss_index_path


def test_reads_env_vars(monkeypatch):
    monkeypatch.setenv("EMBEDDING_MODEL", "custom-model")
    monkeypatch.setenv("EMBEDDING_DIMENSION", "4")
    settings = EmbeddingSettings()
    assert settings.model == "custom-model"
    assert settings.dimension == 4


def test_redis_defaults():
    settings = EmbeddingSettings()
    assert settings.redis_url.startswith("redis://")
    assert settings.cache_ttl_seconds > 0


def test_redis_reads_env_vars(monkeypatch):
    monkeypatch.setenv("EMBEDDING_REDIS_URL", "redis://cache-host:6379/2")
    monkeypatch.setenv("EMBEDDING_CACHE_TTL_SECONDS", "60")
    settings = EmbeddingSettings()
    assert settings.redis_url == "redis://cache-host:6379/2"
    assert settings.cache_ttl_seconds == 60
