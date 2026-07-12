from __future__ import annotations

import time

import pytest
from jose import jwt

from app.auth.deps import require_admin
from app.auth.service import create_access_token, decode_token
from app.core.config import get_config
from app.core.errors import AuthError, ForbiddenError
from app.db.models import User
from app.db.models.enums import UserRole


def test_token_roundtrip() -> None:
    token = create_access_token("user-123")
    assert decode_token(token)["sub"] == "user-123"


def test_decode_rejects_garbage() -> None:
    with pytest.raises(AuthError):
        decode_token("not-a-real-token")


def test_decode_rejects_expired_token() -> None:
    config = get_config()
    token = jwt.encode(
        {"sub": "x", "exp": int(time.time()) - 10},
        config.get("secret_key"),
        algorithm=config.get("jwt_algorithm"),
    )
    with pytest.raises(AuthError):
        decode_token(token)


async def test_require_admin_blocks_non_admin() -> None:
    user = User(email="u@example.com", hashed_password="x", role=UserRole.user)
    with pytest.raises(ForbiddenError):
        await require_admin(user)


async def test_require_admin_allows_admin() -> None:
    admin = User(email="a@example.com", hashed_password="x", role=UserRole.admin)
    assert await require_admin(admin) is admin
