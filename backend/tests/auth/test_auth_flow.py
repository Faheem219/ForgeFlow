from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.api.app import create_app
from app.db.repos import UserRepo

# Every test in this module runs the HTTP app in-process against a disposable DB.
pytestmark = pytest.mark.usefixtures("mongo_db")


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


async def test_register_login_me_happy_path(client: AsyncClient) -> None:
    reg = await client.post(
        "/auth/register", json={"email": "A@Example.com", "password": "password123"}
    )
    assert reg.status_code == 201
    body = reg.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["user"]["email"] == "a@example.com"  # normalized
    assert "hashed_password" not in body["user"]

    login = await client.post(
        "/auth/login", json={"email": "a@example.com", "password": "password123"}
    )
    assert login.status_code == 200
    token = login.json()["access_token"]

    me = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == "a@example.com"
    assert me.json()["role"] == "user"
    assert "hashed_password" not in me.json()


async def test_duplicate_email_conflicts(client: AsyncClient) -> None:
    payload = {"email": "dup@example.com", "password": "password123"}
    assert (await client.post("/auth/register", json=payload)).status_code == 201
    dup = await client.post("/auth/register", json=payload)
    assert dup.status_code == 409
    assert dup.json()["error"]["type"] == "conflict"


async def test_bad_password_is_unauthorized(client: AsyncClient) -> None:
    await client.post("/auth/register", json={"email": "c@example.com", "password": "password123"})
    bad = await client.post(
        "/auth/login", json={"email": "c@example.com", "password": "wrongpassword"}
    )
    assert bad.status_code == 401
    assert bad.json()["error"]["type"] == "auth_error"


async def test_me_requires_authentication(client: AsyncClient) -> None:
    assert (await client.get("/auth/me")).status_code == 401


async def test_password_stored_as_bcrypt_hash(client: AsyncClient) -> None:
    await client.post("/auth/register", json={"email": "h@example.com", "password": "password123"})
    user = await UserRepo().get_by_email("h@example.com")
    assert user is not None
    assert user.hashed_password != "password123"
    assert user.hashed_password.startswith("$2")  # bcrypt prefix
