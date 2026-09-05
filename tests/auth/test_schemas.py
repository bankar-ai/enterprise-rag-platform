import pytest
from pydantic import ValidationError

from app.auth.schemas import LoginRequest, RegisterRequest, TokenResponse


def test_register_request_rejects_short_password():
    with pytest.raises(ValidationError):
        RegisterRequest(email="a@example.com", password="short")


def test_register_request_accepts_valid_input():
    request = RegisterRequest(email="a@example.com", password="a-long-enough-password")
    assert request.email == "a@example.com"


def test_login_request_requires_both_fields():
    with pytest.raises(ValidationError):
        LoginRequest(email="a@example.com")


def test_token_response_defaults_token_type_to_bearer():
    response = TokenResponse(access_token="a", refresh_token="b")
    assert response.token_type == "bearer"
