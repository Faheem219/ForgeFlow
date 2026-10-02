"""After a Deploy reset, the Deploy and Validate panels read as never run (requested 2026-10-02).

"Completely blank, as if nothing was done yet" — so the reads the two panels hydrate from stop
returning what was produced before the reset. Nothing is deleted: the retired deployment and the old
validation evidence stay in the store as history; they just stop being the stage's current result.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from beanie import PydanticObjectId
from httpx import ASGITransport, AsyncClient

from app.api.app import create_app
from app.db.models import Deployment, TestRun
from app.db.models.enums import ArtifactType, DeployMode, Stage, TestEnv
from app.deploy.providers.base import DeployRef
from app.orchestrator.artifacts import ArtifactService
from app.orchestrator.stages.validate import VALIDATION_REPORT_KIND

pytestmark = pytest.mark.usefixtures("mongo_db", "blob_env", "fernet_key")


class _Provider:
    async def destroy(self, ref: DeployRef, **_: Any) -> bool:
        return True


@pytest_asyncio.fixture
async def client(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    from app.deploy import teardown as teardown_mod

    monkeypatch.setattr(teardown_mod, "provider_for", lambda _target: _Provider())
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


async def _setup(client: AsyncClient, email: str) -> tuple[dict[str, str], str]:
    resp = await client.post("/auth/register", json={"email": email, "password": "password123"})
    headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}
    pid: str = (await client.post("/projects", json={"name": "p"}, headers=headers)).json()["id"]
    for stage in ("build", "deploy", "validate"):
        await client.post(
            f"/projects/{pid}/stages/{stage}/transition",
            json={"action": "complete"},
            headers=headers,
        )
    return headers, pid


async def _deployment(pid: str) -> Deployment:
    return await Deployment(
        project_id=PydanticObjectId(pid),
        mode=DeployMode.seamless,
        urls={"fe": "https://web"},
        status="live",
    ).insert()


async def _validation(pid: str) -> None:
    await ArtifactService().create_version(
        PydanticObjectId(pid),
        Stage.validate,
        ArtifactType.test_result,
        text=json.dumps({"outcome": "validated", "url": "https://web"}),
        meta={"kind": VALIDATION_REPORT_KIND},
    )
    await TestRun(
        project_id=PydanticObjectId(pid),
        env=TestEnv.live,
        results=[
            {
                "file": "e2e/a.spec.ts",
                "name": "loads",
                "status": "passed",
                "framework": "playwright",
            }
        ],
    ).insert()


async def _intent(client: AsyncClient, headers: dict[str, str], pid: str, stage: str) -> Any:
    resp = await client.post(
        f"/projects/{pid}/intent", json={"stage": stage, "action": "reset"}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_the_deploy_panel_is_blank_after_a_reset(client: AsyncClient) -> None:
    headers, pid = await _setup(client, "blank@deploy.test")
    await _deployment(pid)
    assert (await client.get(f"/projects/{pid}/deploy/latest", headers=headers)).json()

    body = await _intent(client, headers, pid, "deploy")

    assert body["to_status"] == "empty"
    assert body["cleared"] == ["deploy", "validate"]
    for path in ("deploy/latest", "deploy/latest/logs"):
        resp = await client.get(f"/projects/{pid}/{path}", headers=headers)
        assert resp.status_code == 200
        assert resp.json() is None, path
    # Nothing to delete any more — the old record is history, not the current deployment.
    resp = await client.delete(f"/projects/{pid}/deploy/latest", headers=headers)
    assert resp.status_code == 404
    # ...but it was kept, not destroyed.
    assert await Deployment.find({"project_id": PydanticObjectId(pid)}).count() == 1


async def test_a_deploy_after_the_reset_is_shown(client: AsyncClient) -> None:
    headers, pid = await _setup(client, "fresh@deploy.test")
    await _deployment(pid)
    await _intent(client, headers, pid, "deploy")

    fresh = await _deployment(pid)

    body = (await client.get(f"/projects/{pid}/deploy/latest", headers=headers)).json()
    assert body["id"] == str(fresh.id)


async def test_the_validate_panel_is_blank_after_a_deploy_reset(client: AsyncClient) -> None:
    headers, pid = await _setup(client, "vblank@deploy.test")
    await _deployment(pid)
    await _validation(pid)
    assert (await client.get(f"/projects/{pid}/validate/latest", headers=headers)).json()
    assert (await client.get(f"/projects/{pid}/validate/live-run", headers=headers)).json()

    await _intent(client, headers, pid, "deploy")

    for path in ("validate/latest", "validate/live-run"):
        resp = await client.get(f"/projects/{pid}/{path}", headers=headers)
        assert resp.status_code == 200
        assert resp.json() is None, path


async def test_new_validation_after_a_reset_is_shown(client: AsyncClient) -> None:
    headers, pid = await _setup(client, "vfresh@deploy.test")
    await _validation(pid)
    await _intent(client, headers, pid, "validate")

    await _validation(pid)

    assert (await client.get(f"/projects/{pid}/validate/latest", headers=headers)).json()
    assert (await client.get(f"/projects/{pid}/validate/live-run", headers=headers)).json()


async def test_a_status_only_reset_hides_nothing(client: AsyncClient) -> None:
    """Build and Test keep showing their work — only the "done" mark is taken back."""
    headers, pid = await _setup(client, "keep@deploy.test")
    await _deployment(pid)
    await _validation(pid)

    body = await _intent(client, headers, pid, "build")

    assert body["to_status"] == "awaiting_user"
    assert body["cleared"] == []
    assert (await client.get(f"/projects/{pid}/deploy/latest", headers=headers)).json()
    assert (await client.get(f"/projects/{pid}/validate/latest", headers=headers)).json()
