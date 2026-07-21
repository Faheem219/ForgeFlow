"""Deploy read API (phase-38): what the topology view hydrates from on load.

A reload has no ``deploy.status`` events to replay, so the graph must be reconstructable from the
persisted ``Deployment`` alone — and the payload must stay secret-safe (§7).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from beanie import PydanticObjectId
from httpx import ASGITransport, AsyncClient

from app.api.app import create_app
from app.db.blobs import get_blob_store
from app.db.models import Deployment
from app.db.models.enums import DeployMode

pytestmark = pytest.mark.usefixtures("mongo_db", "blob_env")

_TOPOLOGY = {
    "nodes": [
        {
            "id": "fe",
            "kind": "frontend",
            "provider": "vercel",
            "url": "https://web",
            "status": "live",
        },
        {
            "id": "be",
            "kind": "backend",
            "provider": "render",
            "url": "https://api",
            "status": "live",
        },
        {"id": "db", "kind": "database", "provider": "platform", "status": "ready"},
    ],
    "edges": [
        {"source": "fe", "target": "be", "label": "VITE_API_BASE_URL"},
        {"source": "be", "target": "db", "label": "MONGODB_URI"},
    ],
    "status": "live",
}


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


async def _insert_deployment(project_id: str, status: str, *, log: str | None = None) -> Deployment:
    logs_ref = await get_blob_store().put(log.encode("utf-8")) if log else None
    return await Deployment(
        project_id=PydanticObjectId(project_id),
        mode=DeployMode.seamless,
        fe_target="vercel",
        be_target="render",
        db_target="platform",
        urls={"fe": "https://web", "be": "https://api"},
        status=status,
        topology_snapshot=_TOPOLOGY,
        logs_ref=logs_ref,
    ).insert()


async def test_latest_is_null_before_the_first_deploy(client: AsyncClient) -> None:
    token = await _register(client, "never@deployed.test")
    pid = await _project(client, token)

    resp = await client.get(f"/projects/{pid}/deploy/latest", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.json() is None


async def test_latest_returns_the_newest_deployment_with_its_topology(client: AsyncClient) -> None:
    token = await _register(client, "topology@deployed.test")
    pid = await _project(client, token)
    await _insert_deployment(pid, "degraded")
    await _insert_deployment(pid, "live")  # the re-deploy the graph should show

    resp = await client.get(f"/projects/{pid}/deploy/latest", headers=_auth(token))
    body = resp.json()

    assert body["status"] == "live"
    assert body["urls"] == {"fe": "https://web", "be": "https://api"}
    assert {n["id"] for n in body["topology_snapshot"]["nodes"]} == {"fe", "be", "db"}
    assert body["mode"] == "seamless"


async def test_latest_carries_no_secrets(client: AsyncClient) -> None:
    """A Deployment records URLs and shape — never env values (§7), so neither does this DTO."""
    token = await _register(client, "safe@deployed.test")
    pid = await _project(client, token)
    await _insert_deployment(pid, "live")

    resp = await client.get(f"/projects/{pid}/deploy/latest", headers=_auth(token))

    assert "mongodb://" not in resp.text and "mongodb+srv://" not in resp.text
    assert "env" not in resp.json()


async def test_logs_return_the_recorded_step_log(client: AsyncClient) -> None:
    token = await _register(client, "logs@deployed.test")
    pid = await _project(client, token)
    await _insert_deployment(pid, "live", log="db: provisioned (platform)\nbe: live https://api")

    resp = await client.get(f"/projects/{pid}/deploy/latest/logs", headers=_auth(token))
    body = resp.json()

    assert "db: provisioned (platform)" in body["log"]
    assert "be: live https://api" in body["log"]


async def test_logs_are_empty_when_none_were_stored(client: AsyncClient) -> None:
    token = await _register(client, "nolog@deployed.test")
    pid = await _project(client, token)
    await _insert_deployment(pid, "failed")

    resp = await client.get(f"/projects/{pid}/deploy/latest/logs", headers=_auth(token))
    assert resp.json()["log"] == ""


async def test_another_users_deployment_is_not_readable(client: AsyncClient) -> None:
    owner = await _register(client, "owner@deployed.test")
    pid = await _project(client, owner)
    await _insert_deployment(pid, "live")

    intruder = await _register(client, "intruder@deployed.test")
    resp = await client.get(f"/projects/{pid}/deploy/latest", headers=_auth(intruder))

    assert resp.status_code == 404  # existence is never leaked
