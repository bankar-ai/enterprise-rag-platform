"""Pydantic schemas for the auth API's requests, responses, and resolved identity.

`email` fields are plain `str` with a lightweight shape check, not Pydantic's `EmailStr` —
`EmailStr` requires the separate `email-validator` package, and full RFC email validation
isn't worth a new dependency here (an invalid email just fails at registration/login with
no matching user, which is already handled).
"""

import re
import uuid
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Role = Literal["admin", "user"]

_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _validate_email_shape(value: str) -> str:
    if not _EMAIL_PATTERN.match(value):
        raise ValueError("must be a valid email address")
    return value


class CurrentUser(BaseModel):
    """The authenticated caller, resolved from a validated access token."""

    id: uuid.UUID
    role: Role


class RegisterRequest(BaseModel):
    """A new-user registration request. Always registers with the `user` role."""

    email: str
    password: str = Field(min_length=8)

    _validate_email = field_validator("email")(_validate_email_shape)


class UserResponse(BaseModel):
    """A registered user's public profile."""

    id: uuid.UUID
    email: str
    role: Role


class LoginRequest(BaseModel):
    """An email + password login request."""

    email: str
    password: str

    _validate_email = field_validator("email")(_validate_email_shape)


class TokenResponse(BaseModel):
    """An issued access + refresh token pair."""

    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"


class RefreshRequest(BaseModel):
    """A refresh-token rotation request."""

    refresh_token: str


class LogoutRequest(BaseModel):
    """A refresh-token revocation request."""

    refresh_token: str
