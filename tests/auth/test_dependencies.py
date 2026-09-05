import uuid

import pytest
from fastapi import HTTPException

from app.auth.dependencies import get_current_user, require_role
from app.auth.security import create_access_token


def test_get_current_user_accepts_valid_token(auth_settings, monkeypatch):
    monkeypatch.setattr("app.auth.dependencies.get_auth_settings", lambda: auth_settings)
    token = create_access_token(uuid.uuid4(), "user", auth_settings)

    current_user = get_current_user(token)

    assert current_user.role == "user"


def test_get_current_user_rejects_invalid_token(auth_settings, monkeypatch):
    monkeypatch.setattr("app.auth.dependencies.get_auth_settings", lambda: auth_settings)
    with pytest.raises(HTTPException) as exc_info:
        get_current_user("garbage")
    assert exc_info.value.status_code == 401


def test_require_role_allows_matching_role(auth_settings, monkeypatch):
    monkeypatch.setattr("app.auth.dependencies.get_auth_settings", lambda: auth_settings)
    token = create_access_token(uuid.uuid4(), "admin", auth_settings)
    current_user = get_current_user(token)

    checker = require_role("admin")
    assert checker(current_user).role == "admin"


def test_require_role_rejects_wrong_role(auth_settings, monkeypatch):
    monkeypatch.setattr("app.auth.dependencies.get_auth_settings", lambda: auth_settings)
    token = create_access_token(uuid.uuid4(), "user", auth_settings)
    current_user = get_current_user(token)

    checker = require_role("admin")
    with pytest.raises(HTTPException) as exc_info:
        checker(current_user)
    assert exc_info.value.status_code == 403
