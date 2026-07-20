"""Partial failure (phase-37, risk §12): a live backend with a failed frontend degrades gracefully —
the BE URL is kept, the failure is recorded clearly, and a re-deploy is safe."""

from __future__ import annotations

import pytest

from app.db.models.enums import DeployMode
from app.deploy.orchestrate import STATUS_DEGRADED, STATUS_FAILED, STATUS_LIVE, DeployOrchestrator
from app.deploy.providers.base import DeployTarget
from tests.deploy.orchestrate_fakes import (
    FakeDeployProvider,
    FakeFrontendBuilder,
    RecordingEmitter,
    factory_of,
    full_plan,
    project_with_build,
    static_plan_source,
)

pytestmark = pytest.mark.usefixtures("mongo_db", "fernet_key", "blob_env")

_BE_URL = "https://api.onrender.com"
_FE_URL = "https://web.vercel.app"


def _orchestrator(
    fe: FakeDeployProvider, be: FakeDeployProvider, emitter: RecordingEmitter
) -> DeployOrchestrator:
    return DeployOrchestrator(
        plan_source=static_plan_source(full_plan()),
        frontend_builder=FakeFrontendBuilder(),
        provider_factory=factory_of(fe, be),
        emitter=emitter,
    )


async def test_frontend_failure_degrades_but_keeps_the_backend() -> None:
    emitter = RecordingEmitter()
    fe = FakeDeployProvider("vercel", DeployTarget.fe, _FE_URL, fail=True)
    be = FakeDeployProvider("render", DeployTarget.be, _BE_URL)
    project = await project_with_build()

    deployment = await _orchestrator(fe, be, emitter).deploy(project, mode=DeployMode.seamless)

    assert deployment.status == STATUS_DEGRADED
    assert deployment.urls == {"be": _BE_URL}  # BE kept; FE absent
    # The failure is surfaced (not swallowed) as a step event.
    fe_events = [p for _e, p in emitter.events if p.get("step") == "fe"]
    assert any(p.get("status") == "failed" and p.get("error") for p in fe_events)
    # The topology records the FE as failed so the UI can show the partial state.
    fe_node = next(n for n in deployment.topology_snapshot["nodes"] if n["id"] == "fe")
    assert fe_node["status"] == "failed"


async def test_backend_failure_is_reported_as_failed() -> None:
    fe = FakeDeployProvider("vercel", DeployTarget.fe, _FE_URL)
    be = FakeDeployProvider("render", DeployTarget.be, _BE_URL, fail=True)
    project = await project_with_build()

    deployment = await _orchestrator(fe, be, RecordingEmitter()).deploy(
        project, mode=DeployMode.seamless
    )

    assert deployment.status == STATUS_FAILED
    # The frontend is never attempted when there is no backend URL to wire it to.
    assert fe.deploy_specs == []


async def test_a_redeploy_after_a_partial_failure_is_safe() -> None:
    project = await project_with_build()

    # First attempt: FE broken → degraded.
    broken_fe = FakeDeployProvider("vercel", DeployTarget.fe, _FE_URL, fail=True)
    be1 = FakeDeployProvider("render", DeployTarget.be, _BE_URL)
    first = await _orchestrator(broken_fe, be1, RecordingEmitter()).deploy(
        project, mode=DeployMode.seamless
    )
    assert first.status == STATUS_DEGRADED

    # Retry with a healthy FE → live, and a brand-new Deployment record (idempotent).
    fixed_fe = FakeDeployProvider("vercel", DeployTarget.fe, _FE_URL)
    be2 = FakeDeployProvider("render", DeployTarget.be, _BE_URL)
    second = await _orchestrator(fixed_fe, be2, RecordingEmitter()).deploy(
        project, mode=DeployMode.seamless
    )
    assert second.status == STATUS_LIVE
    assert second.id != first.id
