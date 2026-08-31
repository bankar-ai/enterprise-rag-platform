"""Generation settings, loaded from environment variables."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class GenerationSettings(BaseSettings):
    """Configuration for LLM-backed answer generation.

    Overridable via `GENERATION_*` env vars.
    """

    model_config = SettingsConfigDict(env_prefix="GENERATION_")

    ollama_host: str = "http://localhost:11434"
    model: str = "qwen3"
    max_context_chars: int = 8000
    temperature: float = 0.1


@lru_cache
def get_generation_settings() -> GenerationSettings:
    """Return the process-wide cached `GenerationSettings` instance."""
    return GenerationSettings()
