"""Auth API: registration, login, refresh-token rotation, and logout."""

import uuid
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth.dependencies import require_role
from app.auth.schemas import (
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    Role,
    TokenResponse,
    UpdateUserActiveRequest,
    UserResponse,
)
from app.auth.service import (
    AccountDisabledError,
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
    InvalidRefreshTokenError,
    UserNotFoundError,
    list_all_users,
    refresh_access_token,
    register_user,
    revoke_user_sessions,
    set_user_active_status,
)
from app.auth.service import login as login_user
from app.auth.service import logout as logout_user

router = APIRouter(prefix="/auth", tags=["auth"])
admin_router = APIRouter(
    prefix="/admin/users", tags=["admin"], dependencies=[Depends(require_role("admin"))]
)


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(request: RegisterRequest) -> UserResponse:
    """Register a new user with the `user` role."""
    try:
        user = register_user(request.email, request.password)
    except EmailAlreadyRegisteredError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email already registered"
        ) from exc
    return UserResponse(id=user.id, email=user.email, role=cast(Role, user.role), is_active=user.is_active)


@router.post("/login")
def login(request: LoginRequest) -> TokenResponse:
    """Exchange email + password for an access + refresh token pair."""
    try:
        return login_user(request.email, request.password)
    except InvalidCredentialsError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password"
        ) from exc
    except AccountDisabledError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Account disabled"
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
    except AccountDisabledError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Account disabled"
        ) from exc


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: LogoutRequest) -> None:
    """Revoke a refresh token."""
    logout_user(request.refresh_token)


@admin_router.get("")
def list_users_endpoint() -> list[UserResponse]:
    """List every registered user. Requires the `admin` role."""
    return [
        UserResponse(id=user.id, email=user.email, role=cast(Role, user.role), is_active=user.is_active)
        for user in list_all_users()
    ]


@admin_router.patch("/{user_id}")
def update_user_active(user_id: uuid.UUID, request: UpdateUserActiveRequest) -> UserResponse:
    """Enable or disable a user's account. Requires the `admin` role."""
    try:
        user = set_user_active_status(user_id, request.is_active)
    except UserNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found") from exc
    return UserResponse(id=user.id, email=user.email, role=cast(Role, user.role), is_active=user.is_active)


@admin_router.post("/{user_id}/revoke-sessions", status_code=status.HTTP_204_NO_CONTENT)
def revoke_sessions(user_id: uuid.UUID) -> None:
    """Revoke every active refresh token for a user, forcing re-authentication. Requires the `admin` role."""
    try:
        revoke_user_sessions(user_id)
    except UserNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found") from exc
