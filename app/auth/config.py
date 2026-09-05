"""Authentication settings, loaded from environment variables.

`jwt_secret_key` has no default — it must come from `AUTH_JWT_SECRET_KEY` — since a
hardcoded signing secret would let anyone forge access tokens (see CLAUDE.md's
never-hardcode-secrets rule).
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class AuthSettings(BaseSettings):
    """Configuration for JWT signing, token lifetimes, and the revocation cache.

    Overridable via `AUTH_*` env vars.
    """

    model_config = SettingsConfigDict(env_prefix="AUTH_")

    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 30
    redis_url: str = "redis://localhost:6379/0"
    redis_socket_timeout_seconds: float = 2.0


@lru_cache
def get_auth_settings() -> AuthSettings:
    """Return the process-wide cached `AuthSettings` instance."""
    return AuthSettings()  # type: ignore[call-arg]
