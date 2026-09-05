"""Shared test helper for registering + logging in a throwaway user against a live TestClient."""

import uuid

from fastapi.testclient import TestClient


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
