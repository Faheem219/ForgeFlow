"""Auth service: password hashing (bcrypt), JWT issue/verify, register/authenticate."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from beanie import PydanticObjectId
from bson.errors import InvalidId
from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import get_config
from app.core.errors import AuthError, ConflictError
from app.db.models import User
from app.db.repos import UserRepo

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return str(_pwd_context.hash(password))


def verify_password(password: str, hashed: str) -> bool:
    return bool(_pwd_context.verify(password, hashed))


def create_access_token(subject: str) -> str:
    config = get_config()
    expire = datetime.now(UTC) + timedelta(minutes=int(config.get("access_token_expire_minutes")))
    payload: dict[str, Any] = {"sub": subject, "exp": expire}
    token: str = jwt.encode(
        payload, config.get("secret_key"), algorithm=config.get("jwt_algorithm")
    )
    return token


def decode_token(token: str) -> dict[str, Any]:
    config = get_config()
    try:
        payload: dict[str, Any] = jwt.decode(
            token, config.get("secret_key"), algorithms=[config.get("jwt_algorithm")]
        )
        return payload
    except JWTError as exc:
        raise AuthError("Invalid or expired token") from exc


class AuthService:
    def __init__(self) -> None:
        self._users = UserRepo()

    async def register(self, email: str, password: str) -> User:
        email = email.strip().lower()
        if await self._users.get_by_email(email) is not None:
            raise ConflictError("Email already registered", detail={"email": email})
        return await self._users.insert(User(email=email, hashed_password=hash_password(password)))

    async def authenticate(self, email: str, password: str) -> User:
        user = await self._users.get_by_email(email.strip().lower())
        if user is None or not verify_password(password, user.hashed_password):
            raise AuthError("Invalid email or password")
        return user

    async def get_user_by_id(self, user_id: str) -> User | None:
        try:
            oid = PydanticObjectId(user_id)
        except (InvalidId, ValueError, TypeError):
            return None
        return await self._users.get(oid)
