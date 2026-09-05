from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_register_then_login_then_refresh_then_logout():
    register_response = client.post(
        "/auth/register",
        json={"email": "router-test@example.com", "password": "a-long-enough-password"},
    )
    assert register_response.status_code == 201
    assert register_response.json()["role"] == "user"

    login_response = client.post(
        "/auth/login",
        json={"email": "router-test@example.com", "password": "a-long-enough-password"},
    )
    assert login_response.status_code == 200
    tokens = login_response.json()
    assert tokens["access_token"]
    assert tokens["refresh_token"]

    refresh_response = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert refresh_response.status_code == 200
    new_tokens = refresh_response.json()
    assert new_tokens["refresh_token"] != tokens["refresh_token"]

    logout_response = client.post("/auth/logout", json={"refresh_token": new_tokens["refresh_token"]})
    assert logout_response.status_code == 204

    reuse_response = client.post(
        "/auth/refresh", json={"refresh_token": new_tokens["refresh_token"]}
    )
    assert reuse_response.status_code == 401


def test_register_rejects_duplicate_email():
    client.post(
        "/auth/register",
        json={"email": "dup-router-test@example.com", "password": "a-long-enough-password"},
    )
    response = client.post(
        "/auth/register",
        json={"email": "dup-router-test@example.com", "password": "another-password"},
    )
    assert response.status_code == 409


def test_login_rejects_wrong_password():
    client.post(
        "/auth/register",
        json={"email": "wrongpw-router-test@example.com", "password": "a-long-enough-password"},
    )
    response = client.post(
        "/auth/login",
        json={"email": "wrongpw-router-test@example.com", "password": "not-the-password"},
    )
    assert response.status_code == 401


def test_refresh_rejects_unknown_token():
    response = client.post("/auth/refresh", json={"refresh_token": "not-a-real-token"})
    assert response.status_code == 401
