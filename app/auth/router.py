"""Auth API: registration, login, refresh-token rotation, and logout."""

from typing import cast

from fastapi import APIRouter, HTTPException, status

from app.auth.schemas import (
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    Role,
    TokenResponse,
    UserResponse,
)
from app.auth.service import (
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
    InvalidRefreshTokenError,
    refresh_access_token,
    register_user,
)
from app.auth.service import login as login_user
from app.auth.service import logout as logout_user

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(request: RegisterRequest) -> UserResponse:
    """Register a new user with the `user` role."""
    try:
        user = register_user(request.email, request.password)
    except EmailAlreadyRegisteredError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email already registered"
        ) from exc
    return UserResponse(id=user.id, email=user.email, role=cast(Role, user.role))


@router.post("/login")
def login(request: LoginRequest) -> TokenResponse:
    """Exchange email + password for an access + refresh token pair."""
    try:
        return login_user(request.email, request.password)
    except InvalidCredentialsError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password"
        ) from exc


@router.post("/refresh")
def refresh(request: RefreshRequest) -> TokenResponse:
    """Rotate a refresh token for a new access + refresh token pair."""
    try:
        return refresh_access_token(request.refresh_token)
    except InvalidRefreshTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired refresh token"
        ) from exc


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: LogoutRequest) -> None:
    """Revoke a refresh token."""
    logout_user(request.refresh_token)
