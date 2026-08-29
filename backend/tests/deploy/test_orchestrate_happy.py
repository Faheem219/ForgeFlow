"""Happy path (phase-37): one action takes a passing build to a healthy live deployment with FE + BE
URLs, the DB wired, in the right order — and records a ``Deployment`` with per-step events."""

from __future__ import annotations

import pytest

from app.db.models.enums import DeployMode
from app.deploy.orchestrate import STATUS_LIVE, DeployOrchestrator
from app.deploy.providers.base import DeployTarget
from app.realtime.schemas import EventType
from tests.deploy.orchestrate_fakes import (
    FakeBackendSource,
    FakeDeployProvider,
    FakeFrontendBuilder,
    RecordingEmitter,
    factory_of,
    full_plan,
    project_with_build,
    static_plan_source,
)

pytestmark = pytest.mark.usefixtures("mongo_db", "fernet_key", "blob_env", "deployable_db")

_BE_URL = "https://forgeflow-api.onrender.com"
_FE_URL = "https://forgeflow-web.vercel.app"


def _orchestrator(
    emitter: RecordingEmitter,
) -> tuple[DeployOrchestrator, FakeDeployProvider, FakeDeployProvider, FakeFrontendBuilder]:
    fe = FakeDeployProvider("vercel", DeployTarget.fe, _FE_URL)
    be = FakeDeployProvider("render", DeployTarget.be, _BE_URL)
    builder = FakeFrontendBuilder()
    orch = DeployOrchestrator(
        plan_source=static_plan_source(full_plan()),
        frontend_builder=builder,
        backend_source=FakeBackendSource(),
        provider_factory=factory_of(fe, be),
        emitter=emitter,
    )
    return orch, fe, be, builder


async def test_deploy_reaches_live_with_both_urls() -> None:
    emitter = RecordingEmitter()
    orch, fe, be, _builder = _orchestrator(emitter)
    project = await project_with_build()

    deployment = await orch.deploy(project, mode=DeployMode.seamless)

    assert deployment.status == STATUS_LIVE
    assert deployment.urls == {"be": _BE_URL, "fe": _FE_URL}
    assert deployment.fe_target == "vercel" and deployment.be_target == "render"
    assert deployment.db_target == "platform"
    assert deployment.mode is DeployMode.seamless
    assert len(fe.deploy_specs) == 1 and len(be.deploy_specs) == 1


async def test_topology_snapshot_reflects_the_real_deploy() -> None:
    orch, _fe, _be, _builder = _orchestrator(RecordingEmitter())
    project = await project_with_build()

    deployment = await orch.deploy(project, mode=DeployMode.seamless)
    topo = deployment.topology_snapshot

    node_ids = {n["id"] for n in topo["nodes"]}
    assert node_ids == {"fe", "be", "db"}
    fe_node = next(n for n in topo["nodes"] if n["id"] == "fe")
    assert fe_node["url"] == _FE_URL and fe_node["provider"] == "vercel"
    # The wiring edges are the ones the topology UI (phase-38) draws.
    edges = {(e["source"], e["target"]) for e in topo["edges"]}
    assert edges == {("fe", "be"), ("be", "db")}


async def test_step_events_stream_in_order() -> None:
    emitter = RecordingEmitter()
    orch, _fe, _be, _builder = _orchestrator(emitter)
    project = await project_with_build()

    await orch.deploy(project, mode=DeployMode.seamless)

    assert all(e == str(EventType.deploy_status) for e, _p in emitter.events)
    steps = emitter.steps()
    # Backend is deployed before the frontend (the FE needs the BE URL); health is last.
    assert steps.index("be") < steps.index("fe")
    assert steps[-1] == "health"
    assert {"db", "be", "fe", "health"} <= set(steps)


async def test_deploy_log_is_captured() -> None:
    orch, _fe, _be, _builder = _orchestrator(RecordingEmitter())
    project = await project_with_build()

    deployment = await orch.deploy(project, mode=DeployMode.seamless)
    assert deployment.logs_ref is not None  # the step log was stored for the UI
