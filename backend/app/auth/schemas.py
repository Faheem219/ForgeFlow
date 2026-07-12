from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.db.models import User
from app.db.models.enums import UserRole


def _normalize_email(value: str) -> str:
    value = value.strip().lower()
    local, _, domain = value.partition("@")
    if not local or "." not in domain:
        raise ValueError("invalid email address")
    return value


class RegisterRequest(BaseModel):
    email: str
    password: str = Field(min_length=8, max_length=72)  # 72 = bcrypt byte limit

    @field_validator("email")
    @classmethod
    def _email(cls, value: str) -> str:
        return _normalize_email(value)


class LoginRequest(BaseModel):
    email: str
    password: str


class UserPublic(BaseModel):
    """User projection returned to clients — never includes the password hash."""

    id: str
    email: str
    role: UserRole
    created_at: datetime

    @classmethod
    def from_user(cls, user: User) -> UserPublic:
        return cls(
            id=str(user.id),
            email=user.email,
            role=user.role,
            created_at=user.created_at,
        )


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserPublic
