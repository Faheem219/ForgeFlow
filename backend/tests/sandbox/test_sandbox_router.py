"""Sandbox routes: ownership/auth guards + the ensure→status→stop→destroy happy path.

The manager is backed by the in-memory Docker double so the routes exercise real lifecycle
transitions without a daemon.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.api.app import create_app
from app.sandbox.manager import SandboxManager, set_manager
from tests.sandbox.fakes import FakeDockerClient

pytestmark = pytest.mark.usefixtures("mongo_db")


@pytest.fixture(autouse=True)
def fake_manager() -> Iterator[None]:
    set_manager(SandboxManager(client=FakeDockerClient()))
    yield
    # conftest's _reset_state resets the singleton after the test.


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


async def _register(client: AsyncClient, email: str) -> str:
    resp = await client.post("/auth/register", json={"email": email, "password": "password123"})
    token: str = resp.json()["access_token"]
    return token


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _new_project(client: AsyncClient, token: str) -> str:
    resp = await client.post("/projects", json={"name": "p"}, headers=_auth(token))
    return str(resp.json()["id"])


async def test_lifecycle_happy_path(client: AsyncClient) -> None:
    token = await _register(client, "sb-owner@example.com")
    pid = await _new_project(client, token)
    h = _auth(token)

    absent = await client.get(f"/projects/{pid}/sandbox/status", headers=h)
    assert absent.status_code == 200
    assert absent.json()["status"] == "absent"

    ensured = await client.post(f"/projects/{pid}/sandbox/ensure", headers=h)
    assert ensured.status_code == 200
    body = ensured.json()
    assert body["status"] == "running"
    assert body["container_id"]

    running = await client.get(f"/projects/{pid}/sandbox/status", headers=h)
    assert running.json()["status"] == "running"

    stopped = await client.post(f"/projects/{pid}/sandbox/stop", headers=h)
    assert stopped.status_code == 200
    assert stopped.json()["status"] == "stopped"

    destroyed = await client.post(
        f"/projects/{pid}/sandbox/destroy", json={"remove_volume": True}, headers=h
    )
    assert destroyed.status_code == 200
    assert destroyed.json()["status"] == "absent"


async def test_destroy_without_body_defaults_to_keeping_volume(client: AsyncClient) -> None:
    token = await _register(client, "sb-nobody@example.com")
    pid = await _new_project(client, token)
    h = _auth(token)
    await client.post(f"/projects/{pid}/sandbox/ensure", headers=h)

    destroyed = await client.post(f"/projects/{pid}/sandbox/destroy", headers=h)
    assert destroyed.status_code == 200
    assert destroyed.json()["status"] == "absent"


async def test_requires_authentication(client: AsyncClient) -> None:
    # A syntactically valid but unauthenticated id: guard must reject before touching docker.
    fake_id = "0" * 24
    assert (await client.post(f"/projects/{fake_id}/sandbox/ensure")).status_code == 401
    assert (await client.get(f"/projects/{fake_id}/sandbox/status")).status_code == 401
    assert (await client.post(f"/projects/{fake_id}/sandbox/stop")).status_code == 401
    assert (await client.post(f"/projects/{fake_id}/sandbox/destroy")).status_code == 401


async def test_foreign_project_is_not_found(client: AsyncClient) -> None:
    owner = await _register(client, "sb-a@example.com")
    intruder = await _register(client, "sb-b@example.com")
    pid = await _new_project(client, owner)
    h = _auth(intruder)

    assert (await client.get(f"/projects/{pid}/sandbox/status", headers=h)).status_code == 404
    assert (await client.post(f"/projects/{pid}/sandbox/ensure", headers=h)).status_code == 404
    assert (await client.post(f"/projects/{pid}/sandbox/stop", headers=h)).status_code == 404
    assert (await client.post(f"/projects/{pid}/sandbox/destroy", headers=h)).status_code == 404
