"""HTTP surface for POST /projects/{id}/intent (phase-08)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from beanie import PydanticObjectId
from httpx import ASGITransport, AsyncClient

from app.api.app import create_app

pytestmark = pytest.mark.usefixtures("mongo_db")


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


async def _project(client: AsyncClient, token: str) -> str:
    resp = await client.post("/projects", json={"name": "p"}, headers=_auth(token))
    pid: str = resp.json()["id"]
    return pid


async def test_intent_happy_path_returns_summary(client: AsyncClient) -> None:
    token = await _register(client, "conductor@example.com")
    pid = await _project(client, token)

    resp = await client.post(
        f"/projects/{pid}/intent",
        json={"stage": "test", "action": "proceed", "message": "go"},
        headers=_auth(token),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["stage"] == "test"
    assert body["to_status"] == "complete"
    assert body["run_id"]
    assert len(body["artifacts"]) == 1
    assert any(m["role"] == "assistant" for m in body["messages"])

    # The round shows up in the conversation history.
    msgs = await client.get(f"/projects/{pid}/messages", headers=_auth(token))
    assert [m["content"] for m in msgs.json()][0] == "go"


async def test_intent_illegal_transition_is_user_error(client: AsyncClient) -> None:
    token = await _register(client, "early-deploy@example.com")
    pid = await _project(client, token)

    resp = await client.post(
        f"/projects/{pid}/intent",
        json={"stage": "deploy", "action": "proceed"},
        headers=_auth(token),
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["type"] == "user_error"


async def test_intent_invalid_stage_is_validation_error(client: AsyncClient) -> None:
    token = await _register(client, "bad-stage@example.com")
    pid = await _project(client, token)

    resp = await client.post(
        f"/projects/{pid}/intent",
        json={"stage": "nonsense", "action": "proceed"},
        headers=_auth(token),
    )
    assert resp.status_code == 422


async def test_intent_on_foreign_project_is_404(client: AsyncClient) -> None:
    owner_token = await _register(client, "own@example.com")
    intruder_token = await _register(client, "intrude@example.com")
    pid = await _project(client, owner_token)

    resp = await client.post(
        f"/projects/{pid}/intent",
        json={"stage": "design", "action": "proceed"},
        headers=_auth(intruder_token),
    )
    assert resp.status_code == 404


async def test_intent_requires_auth(client: AsyncClient) -> None:
    ghost = PydanticObjectId()
    resp = await client.post(
        f"/projects/{ghost}/intent", json={"stage": "design", "action": "proceed"}
    )
    assert resp.status_code == 401
