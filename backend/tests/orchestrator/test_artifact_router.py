"""HTTP surface for messages + artifacts + diff, including ownership (phase-07).

Artifacts are seeded through the service (there is no create endpoint this phase) and read back
through the API. Small inline payloads are used so the blob backend is never touched here — the
blob round-trip is covered by ``test_blobs.py`` / ``test_artifact_versioning.py`` against a
disposable DB.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from beanie import PydanticObjectId
from httpx import ASGITransport, AsyncClient

from app.api.app import create_app
from app.db.models.enums import ArtifactType, MessageRole, Stage
from app.orchestrator.artifacts import ArtifactService
from app.orchestrator.messages import MessageService

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


async def _create_project(client: AsyncClient, token: str) -> str:
    resp = await client.post("/projects", json={"name": "p"}, headers=_auth(token))
    pid: str = resp.json()["id"]
    return pid


async def test_list_artifacts_shows_all_versions(client: AsyncClient) -> None:
    token = await _register(client, "art-owner@example.com")
    pid = await _create_project(client, token)
    project_oid = PydanticObjectId(pid)

    await ArtifactService().create_version(
        project_oid, Stage.design, ArtifactType.design, text="v1"
    )
    await ArtifactService().create_version(
        project_oid, Stage.design, ArtifactType.design, text="v2"
    )

    resp = await client.get(
        f"/projects/{pid}/artifacts", params={"type": "design"}, headers=_auth(token)
    )
    assert resp.status_code == 200
    versions = sorted(a["version"] for a in resp.json())
    assert versions == [1, 2]


async def test_get_artifact_returns_resolved_content(client: AsyncClient) -> None:
    token = await _register(client, "content@example.com")
    pid = await _create_project(client, token)
    artifact = await ArtifactService().create_version(
        PydanticObjectId(pid), Stage.design, ArtifactType.design, text="hello world"
    )

    resp = await client.get(f"/artifacts/{artifact.id}", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["version"] == 1
    assert body["content"] == "hello world"


async def test_diff_endpoint_returns_unified_diff(client: AsyncClient) -> None:
    token = await _register(client, "differ@example.com")
    pid = await _create_project(client, token)
    project_oid = PydanticObjectId(pid)
    a = await ArtifactService().create_version(
        project_oid, Stage.build, ArtifactType.code_change, text="a\nb\nc\n"
    )
    b = await ArtifactService().create_version(
        project_oid, Stage.build, ArtifactType.code_change, text="a\nB\nc\n"
    )

    resp = await client.get(f"/artifacts/{a.id}/diff/{b.id}", headers=_auth(token))
    assert resp.status_code == 200
    diff = resp.json()["diff"]
    assert "-b" in diff and "+B" in diff


async def test_list_messages_in_order_with_stage_filter(client: AsyncClient) -> None:
    token = await _register(client, "msgs@example.com")
    pid = await _create_project(client, token)
    project_oid = PydanticObjectId(pid)
    await MessageService().append(project_oid, MessageRole.user, "d", stage=Stage.design)
    await MessageService().append(project_oid, MessageRole.assistant, "b", stage=Stage.build)

    all_msgs = await client.get(f"/projects/{pid}/messages", headers=_auth(token))
    assert [m["content"] for m in all_msgs.json()] == ["d", "b"]

    design = await client.get(
        f"/projects/{pid}/messages", params={"stage": "design"}, headers=_auth(token)
    )
    assert [m["content"] for m in design.json()] == ["d"]


async def test_artifacts_and_messages_are_ownership_checked(client: AsyncClient) -> None:
    owner_token = await _register(client, "owner2@example.com")
    intruder_token = await _register(client, "intruder2@example.com")
    pid = await _create_project(client, owner_token)
    artifact = await ArtifactService().create_version(
        PydanticObjectId(pid), Stage.design, ArtifactType.design, text="secret"
    )

    ih = _auth(intruder_token)
    assert (await client.get(f"/projects/{pid}/artifacts", headers=ih)).status_code == 404
    assert (await client.get(f"/projects/{pid}/messages", headers=ih)).status_code == 404
    assert (await client.get(f"/artifacts/{artifact.id}", headers=ih)).status_code == 404


async def test_get_missing_artifact_is_404(client: AsyncClient) -> None:
    token = await _register(client, "missing@example.com")
    assert (await client.get("/artifacts/not-an-id", headers=_auth(token))).status_code == 404
    ghost = PydanticObjectId()
    assert (await client.get(f"/artifacts/{ghost}", headers=_auth(token))).status_code == 404


async def test_artifact_endpoints_require_auth(client: AsyncClient) -> None:
    ghost = PydanticObjectId()
    assert (await client.get(f"/artifacts/{ghost}")).status_code == 401
