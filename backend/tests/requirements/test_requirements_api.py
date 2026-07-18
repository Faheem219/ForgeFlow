"""Requirements CRUD API + ownership (phase-25)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
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


def _spec(*features: dict[str, object]) -> dict[str, object]:
    return {"features": list(features)}


def _feature(name: str, *texts: str) -> dict[str, object]:
    return {"name": name, "acceptance_criteria": [{"text": t} for t in texts]}


async def test_crud_and_versioning_flow(client: AsyncClient) -> None:
    token = await _register(client, "req@example.com")
    pid = await _project(client, token)

    # No requirements yet.
    assert (
        await client.get(f"/projects/{pid}/requirements", headers=_auth(token))
    ).status_code == 404

    # POST v1 with two features.
    created = await client.post(
        f"/projects/{pid}/requirements",
        json=_spec(_feature("Todos", "can add"), _feature("Auth", "can log in")),
        headers=_auth(token),
    )
    assert created.status_code == 200
    v1 = created.json()
    assert v1["version"] == 1 and len(v1["features"]) == 2
    auth_crit_id = v1["features"][1]["acceptance_criteria"][0]["id"]

    # GET latest.
    got = await client.get(f"/projects/{pid}/requirements", headers=_auth(token))
    assert got.json()["version"] == 1

    # PUT an edit (round-trip Auth's id) → v2.
    edited = await client.put(
        f"/projects/{pid}/requirements",
        json=_spec(
            _feature("Todos", "can add", "can remove"),
            {
                "name": "Auth",
                "acceptance_criteria": [{"id": auth_crit_id, "text": "can log in"}],
            },
        ),
        headers=_auth(token),
    )
    assert edited.status_code == 200
    v2 = edited.json()
    assert v2["version"] == 2
    assert v2["features"][1]["acceptance_criteria"][0]["id"] == auth_crit_id  # preserved

    # Both versions listed.
    versions = await client.get(f"/projects/{pid}/requirements/versions", headers=_auth(token))
    assert [v["version"] for v in versions.json()] == [1, 2]


async def test_invalid_spec_returns_400_with_detail(client: AsyncClient) -> None:
    token = await _register(client, "bad@example.com")
    pid = await _project(client, token)

    resp = await client.post(
        f"/projects/{pid}/requirements",
        json=_spec({"name": "Todos", "acceptance_criteria": []}),  # no criteria
        headers=_auth(token),
    )
    assert resp.status_code == 400
    body = resp.json()["error"]
    assert body["type"] == "user_error"
    assert body["detail"]["feature_index"] == 0


async def test_requirements_are_ownership_checked(client: AsyncClient) -> None:
    owner = await _register(client, "owner@example.com")
    intruder = await _register(client, "intruder@example.com")
    pid = await _project(client, owner)
    await client.post(
        f"/projects/{pid}/requirements",
        json=_spec(_feature("Todos", "can add")),
        headers=_auth(owner),
    )

    ih = _auth(intruder)
    assert (await client.get(f"/projects/{pid}/requirements", headers=ih)).status_code == 404
    assert (
        await client.post(
            f"/projects/{pid}/requirements",
            json=_spec(_feature("X", "y")),
            headers=ih,
        )
    ).status_code == 404
    assert (
        await client.get(f"/projects/{pid}/requirements/versions", headers=ih)
    ).status_code == 404


async def test_requirements_require_auth(client: AsyncClient) -> None:
    assert (await client.get("/projects/whatever/requirements")).status_code == 401
