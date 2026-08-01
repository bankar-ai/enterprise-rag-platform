from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class IngestionSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="INGESTION_")

    chunk_size: int = 1500
    chunk_overlap: int = 200
    ocr_text_threshold: int = 20
    max_upload_size_bytes: int = 50_000_000


@lru_cache
def get_settings() -> IngestionSettings:
    return IngestionSettings()
