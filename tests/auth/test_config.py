import pytest
from pydantic import ValidationError

from app.auth.config import AuthSettings, get_auth_settings


def test_settings_require_jwt_secret_key(monkeypatch):
    monkeypatch.delenv("AUTH_JWT_SECRET_KEY", raising=False)
    with pytest.raises(ValidationError):
        AuthSettings()


def test_settings_have_expected_defaults(auth_settings):
    assert auth_settings.jwt_algorithm == "HS256"
    assert auth_settings.access_token_expire_minutes == 30
    assert auth_settings.refresh_token_expire_days == 30


def test_get_auth_settings_is_cached():
    assert get_auth_settings() is get_auth_settings()
