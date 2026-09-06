"""Shared test helpers for authenticating against a live TestClient."""

import uuid

from fastapi.testclient import TestClient

from app.auth.config import get_auth_settings
from app.auth.repository import create_user
from app.auth.security import create_access_token, hash_password
from app.core.db import get_session_factory


def register_and_login(client: TestClient, prefix: str) -> dict[str, str]:
    """Register a fresh throwaway user (`prefix` + a UUID, so tests never collide) and log in.

    Returns headers ready to pass as `client.post(..., headers=...)`.
    """
    email = f"{prefix}-{uuid.uuid4()}@example.com"
    password = "a-long-enough-password"
    client.post("/auth/register", json={"email": email, "password": password})
    login_response = client.post("/auth/login", json={"email": email, "password": password})
    token = login_response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def create_admin_and_get_headers(prefix: str) -> dict[str, str]:
    """Create a fresh throwaway `admin`-role user and return headers with a valid access token.

    Created directly via the repository, since no self-service admin registration exists yet.
    """
    email = f"{prefix}-{uuid.uuid4()}@example.com"
    session_factory = get_session_factory()
    with session_factory() as session:
        user = create_user(session, email, hash_password("a-long-enough-password"), role="admin")
        session.commit()
        user_id, role = user.id, user.role
    token = create_access_token(user_id, role, get_auth_settings())
    return {"Authorization": f"Bearer {token}"}
