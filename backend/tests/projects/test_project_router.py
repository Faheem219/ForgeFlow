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


async def test_create_list_get_project(client: AsyncClient) -> None:
    token = await _register(client, "owner@example.com")

    create = await client.post("/projects", json={"name": "Todo App"}, headers=_auth(token))
    assert create.status_code == 201
    project = create.json()
    assert project["name"] == "Todo App"
    assert project["current_stage"] == "requirements"  # the entry stage (2026-08-06 reorder)

    listing = await client.get("/projects", headers=_auth(token))
    assert listing.status_code == 200
    assert len(listing.json()) == 1

    fetched = await client.get(f"/projects/{project['id']}", headers=_auth(token))
    assert fetched.status_code == 200
    assert fetched.json()["id"] == project["id"]


async def test_create_project_yields_six_empty_stages(client: AsyncClient) -> None:
    token = await _register(client, "stager@example.com")
    create = await client.post("/projects", json={"name": "p"}, headers=_auth(token))
    project_id = create.json()["id"]

    stages = await client.get(f"/projects/{project_id}/stages", headers=_auth(token))
    assert stages.status_code == 200
    body = stages.json()
    assert len(body) == 6
    assert {s["status"] for s in body} == {"empty"}


async def test_rename_project(client: AsyncClient) -> None:
    token = await _register(client, "renamer@example.com")
    create = await client.post("/projects", json={"name": "old"}, headers=_auth(token))
    project_id = create.json()["id"]

    renamed = await client.patch(
        f"/projects/{project_id}", json={"name": "new"}, headers=_auth(token)
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "new"


async def test_delete_project(client: AsyncClient) -> None:
    token = await _register(client, "deleter@example.com")
    create = await client.post("/projects", json={"name": "gone"}, headers=_auth(token))
    project_id = create.json()["id"]

    deleted = await client.delete(f"/projects/{project_id}", headers=_auth(token))
    assert deleted.status_code == 200
    report = deleted.json()
    assert report["id"] == project_id
    # External teardown fails soft (no Docker / no app DB in the test environment), so the report
    # must carry the outcome fields rather than pretending everything was reclaimed.
    assert {"sandbox_removed", "app_db_dropped", "documents_removed", "warnings"} <= report.keys()
    assert isinstance(report["warnings"], list)

    fetched = await client.get(f"/projects/{project_id}", headers=_auth(token))
    assert fetched.status_code == 404


async def test_transition_endpoint_allows_deploy_after_build_complete(
    client: AsyncClient,
) -> None:
    token = await _register(client, "shipper@example.com")
    create = await client.post("/projects", json={"name": "p"}, headers=_auth(token))
    project_id = create.json()["id"]

    blocked = await client.post(
        f"/projects/{project_id}/stages/deploy/transition",
        json={"action": "enter"},
        headers=_auth(token),
    )
    assert blocked.status_code == 400
    assert blocked.json()["error"]["type"] == "user_error"

    await client.post(
        f"/projects/{project_id}/stages/build/transition",
        json={"action": "complete"},
        headers=_auth(token),
    )
    allowed = await client.post(
        f"/projects/{project_id}/stages/deploy/transition",
        json={"action": "enter"},
        headers=_auth(token),
    )
    assert allowed.status_code == 200
    assert allowed.json()["to_status"] == "in_progress"


async def test_foreign_project_is_not_found_on_every_route(client: AsyncClient) -> None:
    owner_token = await _register(client, "a-owner@example.com")
    intruder_token = await _register(client, "b-intruder@example.com")

    create = await client.post("/projects", json={"name": "private"}, headers=_auth(owner_token))
    project_id = create.json()["id"]

    intruder_headers = _auth(intruder_token)
    assert (
        await client.get(f"/projects/{project_id}", headers=intruder_headers)
    ).status_code == 404
    assert (
        await client.patch(f"/projects/{project_id}", json={"name": "x"}, headers=intruder_headers)
    ).status_code == 404
    assert (
        await client.get(f"/projects/{project_id}/stages", headers=intruder_headers)
    ).status_code == 404
    assert (
        await client.post(
            f"/projects/{project_id}/stages/design/transition",
            json={"action": "enter"},
            headers=intruder_headers,
        )
    ).status_code == 404
    assert (
        await client.delete(f"/projects/{project_id}", headers=intruder_headers)
    ).status_code == 404


async def test_projects_require_authentication(client: AsyncClient) -> None:
    assert (await client.get("/projects")).status_code == 401
    assert (await client.post("/projects", json={"name": "x"})).status_code == 401
