"""Validation read API (phase-40): what the Validate panel hydrates from after a reload."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from beanie import PydanticObjectId
from httpx import ASGITransport, AsyncClient

from app.api.app import create_app
from app.db.models import Project, TestRun
from app.db.models.enums import Stage, StageStatus, TestEnv
from app.db.repos import StageStateRepo
from app.orchestrator.stages.validate import LiveValidationController
from tests.orchestrator.stages.validate_fakes import (
    CODE_FAILURE,
    FE_URL,
    PASSING,
    FakeDeployer,
    FakeLiveRunner,
    FakeRepairLoop,
    FakeRepairResult,
    RecordingEmitter,
)

pytestmark = pytest.mark.usefixtures("mongo_db")


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


async def _auth_project(client: AsyncClient, email: str) -> tuple[str, dict[str, str], Project]:
    reg = await client.post("/auth/register", json={"email": email, "password": "password123"})
    token = reg.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    created = await client.post("/projects", json={"name": "p"}, headers=headers)
    pid = created.json()["id"]

    project = await Project.get(PydanticObjectId(pid))
    assert project is not None
    await StageStateRepo().set_status(project.id, Stage.build, StageStatus.complete)  # type: ignore[arg-type]
    await StageStateRepo().set_status(project.id, Stage.deploy, StageStatus.complete)  # type: ignore[arg-type]
    return pid, headers, project


async def test_latest_is_null_before_validation_has_run(client: AsyncClient) -> None:
    pid, headers, _project = await _auth_project(client, "never@validated.test")

    resp = await client.get(f"/projects/{pid}/validate/latest", headers=headers)
    assert resp.status_code == 200 and resp.json() is None

    live = await client.get(f"/projects/{pid}/validate/live-run", headers=headers)
    assert live.json() is None


async def test_a_verified_report_exposes_the_final_link(client: AsyncClient) -> None:
    pid, headers, project = await _auth_project(client, "verified@validated.test")
    from app.db.models import Deployment
    from app.db.models.enums import DeployMode

    await Deployment(
        project_id=project.id, mode=DeployMode.seamless, urls={"fe": FE_URL}, status="live"
    ).insert()

    await LiveValidationController(
        live_runner=FakeLiveRunner(PASSING), emitter=RecordingEmitter()
    ).run(project)

    body = (await client.get(f"/projects/{pid}/validate/latest", headers=headers)).json()
    assert body["outcome"] == "validated"
    assert body["url"] == FE_URL
    assert len(body["cycles"]) == 1


async def test_an_escalated_report_exposes_the_timeline_and_reason(client: AsyncClient) -> None:
    pid, headers, project = await _auth_project(client, "escalated@validated.test")

    await LiveValidationController(
        live_runner=FakeLiveRunner(CODE_FAILURE),
        repair=FakeRepairLoop(FakeRepairResult("fixed")),
        deployer=FakeDeployer(),
        emitter=RecordingEmitter(),
    ).run(project)

    body = (await client.get(f"/projects/{pid}/validate/latest", headers=headers)).json()
    assert body["outcome"] == "escalated"
    assert body["reason"] == "cycle_cap"
    assert body["summary"]
    assert [c["diagnosis"] for c in body["cycles"]] == ["code"] * len(body["cycles"])


async def test_the_live_run_endpoint_returns_per_test_results(client: AsyncClient) -> None:
    pid, headers, project = await _auth_project(client, "livetests@validated.test")

    await LiveValidationController(
        live_runner=FakeLiveRunner(CODE_FAILURE),
        repair=FakeRepairLoop(FakeRepairResult("escalated")),
        emitter=RecordingEmitter(),
    ).run(project)

    body = (await client.get(f"/projects/{pid}/validate/live-run", headers=headers)).json()
    assert body["total"] == 2 and body["failed"] == 1 and body["green"] is False
    assert {r["criterion_id"] for r in body["results"]} == {"ac-list", "ac-add"}


async def test_a_sandbox_run_is_never_served_as_the_live_run(client: AsyncClient) -> None:
    """The panel must show production results, even when a newer sandbox run exists."""
    pid, headers, project = await _auth_project(client, "sandboxrun@validated.test")

    await LiveValidationController(
        live_runner=FakeLiveRunner(PASSING), emitter=RecordingEmitter()
    ).run(project)
    await TestRun(project_id=project.id, env=TestEnv.sandbox, results=[]).insert()  # newer

    body = (await client.get(f"/projects/{pid}/validate/live-run", headers=headers)).json()
    assert body["total"] == 1 and body["green"] is True


async def test_another_users_validation_is_not_readable(client: AsyncClient) -> None:
    pid, _headers, project = await _auth_project(client, "owner@validated.test")
    await LiveValidationController(
        live_runner=FakeLiveRunner(PASSING), emitter=RecordingEmitter()
    ).run(project)

    reg = await client.post(
        "/auth/register", json={"email": "intruder@validated.test", "password": "password123"}
    )
    intruder = {"Authorization": f"Bearer {reg.json()['access_token']}"}

    resp = await client.get(f"/projects/{pid}/validate/latest", headers=intruder)
    assert resp.status_code == 404  # existence is never leaked
