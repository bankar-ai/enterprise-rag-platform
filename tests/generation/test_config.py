from app.generation.config import GenerationSettings, get_generation_settings


def test_default_settings():
    settings = GenerationSettings()
    assert settings.ollama_host == "http://localhost:11434"
    assert settings.model == "qwen3"
    assert settings.max_context_chars == 8000
    assert settings.temperature == 0.1


def test_settings_overridable_via_env(monkeypatch):
    monkeypatch.setenv("GENERATION_MODEL", "qwen3:14b")
    monkeypatch.setenv("GENERATION_MAX_CONTEXT_CHARS", "4000")
    settings = GenerationSettings()
    assert settings.model == "qwen3:14b"
    assert settings.max_context_chars == 4000


def test_get_generation_settings_is_cached():
    get_generation_settings.cache_clear()
    first = get_generation_settings()
    second = get_generation_settings()
    assert first is second
    get_generation_settings.cache_clear()
