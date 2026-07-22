"""Shared fixtures for the phase-51 admin-config suite."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio
from beanie import PydanticObjectId
from httpx import ASGITransport, AsyncClient

from app.api.app import create_app
from app.auth.service import hash_password
from app.core.config import get_config, reset_config
from app.core.config_db import DbSettingProvider, install_db_provider
from app.db.models import User
from app.db.models.enums import UserRole
from app.db.repos import UserRepo


@pytest_asyncio.fixture
async def db_provider() -> DbSettingProvider:
    """Install the DB config layer onto a fresh resolver (as app startup would)."""
    reset_config()
    return await install_db_provider()


async def make_user(role: UserRole = UserRole.user, email: str | None = None) -> User:
    email = email or f"{role}-{PydanticObjectId()}@example.com"
    return await UserRepo().insert(
        User(email=email, hashed_password=hash_password("password123"), role=role)
    )


async def token_for(client: AsyncClient, user: User) -> str:
    resp = await client.post("/auth/login", json={"email": user.email, "password": "password123"})
    assert resp.status_code == 200, resp.text
    return str(resp.json()["access_token"])


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def http() -> AsyncIterator[AsyncClient]:
    # ASGITransport does not run lifespan, so install the DB layer explicitly for API tests.
    reset_config()
    await install_db_provider()
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture
async def admin_client(http: AsyncClient) -> tuple[AsyncClient, dict[str, str]]:
    admin = await make_user(UserRole.admin, "admin@example.com")
    return http, auth(await token_for(http, admin))


__all__ = ["auth", "get_config", "make_user", "token_for"]
