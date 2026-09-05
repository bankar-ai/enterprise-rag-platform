"""Shared fixtures for auth tests: a fixed test-only JWT secret and a dedicated Redis DB."""

import os

os.environ.setdefault("AUTH_JWT_SECRET_KEY", "test-only-secret-do-not-use-in-production")
os.environ.setdefault("AUTH_REDIS_URL", "redis://localhost:6379/3")

import pytest  # noqa: E402
import redis  # noqa: E402

from app.auth.config import AuthSettings  # noqa: E402


@pytest.fixture
def auth_settings() -> AuthSettings:
    """`AuthSettings` pointed at the dedicated test Redis logical DB."""
    return AuthSettings(
        jwt_secret_key=os.environ["AUTH_JWT_SECRET_KEY"],
        redis_url=os.environ["AUTH_REDIS_URL"],
    )


@pytest.fixture(autouse=True)
def _flush_test_redis_db(auth_settings: AuthSettings):
    """Flush the test-only Redis logical DB before and after every auth test."""
    client = redis.Redis.from_url(auth_settings.redis_url)
    client.flushdb()
    yield
    client.flushdb()
