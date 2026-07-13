"""FastAPI dependencies: current-user resolution and admin-role gate."""

from __future__ import annotations

from beanie import PydanticObjectId
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.service import AuthService, decode_token
from app.core.errors import AuthError, ForbiddenError
from app.db.models import User
from app.db.models.enums import UserRole

_bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> User:
    if credentials is None:
        raise AuthError("Not authenticated")
    payload = decode_token(credentials.credentials)
    subject = payload.get("sub")
    if not isinstance(subject, str):
        raise AuthError("Invalid token payload")
    user = await AuthService().get_user_by_id(subject)
    if user is None:
        raise AuthError("User no longer exists")
    return user


async def get_current_user_id(user: User = Depends(get_current_user)) -> PydanticObjectId:
    """The authenticated user's id, narrowed to non-optional (a fetched user always has one)."""
    if user.id is None:  # pragma: no cover - a persisted user always carries an id
        raise AuthError("Invalid user")
    return user.id


async def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != UserRole.admin:
        raise ForbiddenError("Admin privileges required")
    return user
