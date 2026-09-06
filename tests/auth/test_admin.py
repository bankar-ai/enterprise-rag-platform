"""Router-level tests for the admin user-management endpoints."""

import uuid

from fastapi.testclient import TestClient

from app.main import app
from tests.auth_helpers import create_admin_and_get_headers, register_and_login

client = TestClient(app)


def test_non_admin_cannot_list_users():
    headers = register_and_login(client, "admin-forbidden")
    response = client.get("/admin/users", headers=headers)
    assert response.status_code == 403


def test_admin_can_list_users():
    admin_headers = create_admin_and_get_headers("admin-list")
    register_and_login(client, "admin-list-target")

    response = client.get("/admin/users", headers=admin_headers)

    assert response.status_code == 200
    users = response.json()
    assert any(u["email"].startswith("admin-list-target-") for u in users)
    assert all("is_active" in u for u in users)


def test_admin_can_disable_and_reenable_a_user():
    admin_headers = create_admin_and_get_headers("admin-disable")
    email = f"admin-disable-target-{uuid.uuid4()}@example.com"
    password = "a-long-enough-password"
    client.post("/auth/register", json={"email": email, "password": password})
    client.post("/auth/login", json={"email": email, "password": password})
    users = client.get("/admin/users", headers=admin_headers).json()
    user_id = next(u["id"] for u in users if u["email"] == email)

    disable_response = client.patch(
        f"/admin/users/{user_id}", json={"is_active": False}, headers=admin_headers
    )
    assert disable_response.status_code == 200
    assert disable_response.json()["is_active"] is False

    disabled_login = client.post("/auth/login", json={"email": email, "password": password})
    assert disabled_login.status_code == 403

    enable_response = client.patch(
        f"/admin/users/{user_id}", json={"is_active": True}, headers=admin_headers
    )
    assert enable_response.status_code == 200
    assert enable_response.json()["is_active"] is True

    reenabled_login = client.post("/auth/login", json={"email": email, "password": password})
    assert reenabled_login.status_code == 200


def test_disable_unknown_user_returns_404():
    admin_headers = create_admin_and_get_headers("admin-disable-404")
    response = client.patch(
        f"/admin/users/{uuid.uuid4()}", json={"is_active": False}, headers=admin_headers
    )
    assert response.status_code == 404


def test_admin_can_revoke_a_users_sessions():
    admin_headers = create_admin_and_get_headers("admin-revoke")
    email = f"admin-revoke-target-{uuid.uuid4()}@example.com"
    password = "a-long-enough-password"
    client.post("/auth/register", json={"email": email, "password": password})
    login_response = client.post("/auth/login", json={"email": email, "password": password})
    refresh_token = login_response.json()["refresh_token"]

    users = client.get("/admin/users", headers=admin_headers).json()
    user_id = next(u["id"] for u in users if u["email"] == email)

    revoke_response = client.post(f"/admin/users/{user_id}/revoke-sessions", headers=admin_headers)
    assert revoke_response.status_code == 204

    refresh_response = client.post("/auth/refresh", json={"refresh_token": refresh_token})
    assert refresh_response.status_code == 401


def test_revoke_sessions_for_unknown_user_returns_404():
    admin_headers = create_admin_and_get_headers("admin-revoke-404")
    response = client.post(f"/admin/users/{uuid.uuid4()}/revoke-sessions", headers=admin_headers)
    assert response.status_code == 404


def test_admin_endpoints_require_authentication_at_all():
    assert client.get("/admin/users").status_code == 401
    assert client.patch(f"/admin/users/{uuid.uuid4()}", json={"is_active": False}).status_code == 401
    assert client.post(f"/admin/users/{uuid.uuid4()}/revoke-sessions").status_code == 401
