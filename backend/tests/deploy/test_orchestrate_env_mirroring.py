"""Env reaches the provider **project**, not just the deployment (phase-62).

Env passed in a deployment's creation body belongs to that one immutable deployment: it never shows
in the provider's project settings, and any deployment created from the provider's own dashboard
inherits *project* env — of which there was none, so the backend came up with no ``MONGODB_URI``.
The mirror is what closes that, and it must never be able to fail a deploy that already succeeded.
"""

from __future__ import annotations

import pytest

from app.db.models.enums import DeployMode
from app.deploy.db_provision import DbProvisioner
from app.deploy.orchestrate import STATUS_LIVE, DeployOrchestrator
from app.deploy.providers.base import DeployTarget
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

_BE_URL = "https://api.vercel.app"
_FE_URL = "https://web.vercel.app"


def _orchestrator(
    fe: FakeDeployProvider, be: FakeDeployProvider, blobs: object = None
) -> DeployOrchestrator:
    return DeployOrchestrator(
        plan_source=static_plan_source(full_plan()),
        frontend_builder=FakeFrontendBuilder(),
        backend_source=FakeBackendSource(),
        provider_factory=factory_of(fe, be),
        emitter=RecordingEmitter(),
    )


async def test_both_targets_get_their_env_on_the_provider_project() -> None:
    fe = FakeDeployProvider("vercel", DeployTarget.fe, _FE_URL)
    be = FakeDeployProvider("vercel", DeployTarget.be, _BE_URL)
    project = await project_with_build()
    expected_uri = await DbProvisioner().get_app_mongodb_uri(project)

    await _orchestrator(fe, be).deploy(project, mode=DeployMode.seamless)

    # The same map that went inline is also written to the project — one mirror per target.
    assert be.env_calls == [{"NODE_ENV": "production", "MONGODB_URI": expected_uri}]
    assert fe.env_calls == [{"VITE_API_BASE_URL": _BE_URL}]


async def test_the_mirror_targets_the_project_the_deployment_created() -> None:
    """``set_env`` is project-scoped, so it needs the project name off the deploy result — a ref
    with only a deployment id would be a config error at the adapter."""
    fe = FakeDeployProvider("vercel", DeployTarget.fe, _FE_URL)
    be = FakeDeployProvider("vercel", DeployTarget.be, _BE_URL)

    await _orchestrator(fe, be).deploy(await project_with_build(), mode=DeployMode.seamless)

    assert be.env_refs[0].project == be.deploy_specs[0].name
    assert fe.env_refs[0].project == fe.deploy_specs[0].name


async def test_the_mirror_runs_after_the_deploy_not_before() -> None:
    """The provider project does not exist until its first deployment, so the order is fixed."""
    fe = FakeDeployProvider("vercel", DeployTarget.fe, _FE_URL)
    be = FakeDeployProvider("vercel", DeployTarget.be, _BE_URL)

    await _orchestrator(fe, be).deploy(await project_with_build(), mode=DeployMode.seamless)

    # Both recorded exactly one of each; the ref used for the env write came from the deploy.
    assert len(be.deploy_specs) == 1 and len(be.env_refs) == 1
    assert be.env_refs[0].id == "vercel-1"


async def test_a_rejected_env_write_leaves_the_deployment_live() -> None:
    """The deployment is already live and correct when the mirror runs — a failed cosmetic
    follow-up must not turn a good deploy into a failed one."""
    fe = FakeDeployProvider("vercel", DeployTarget.fe, _FE_URL, env_fails=True)
    be = FakeDeployProvider("vercel", DeployTarget.be, _BE_URL, env_fails=True)

    deployment = await _orchestrator(fe, be).deploy(
        await project_with_build(), mode=DeployMode.seamless
    )

    assert deployment.status == STATUS_LIVE
    assert deployment.urls == {"be": _BE_URL, "fe": _FE_URL}


async def test_a_rejected_env_write_is_recorded_in_the_pipeline_log(blob_env: None) -> None:
    """Silently swallowing it would leave the dashboard mysteriously empty with no trace of why."""
    from app.db.blobs import get_blob_store

    fe = FakeDeployProvider("vercel", DeployTarget.fe, _FE_URL)
    be = FakeDeployProvider("vercel", DeployTarget.be, _BE_URL, env_fails=True)

    deployment = await _orchestrator(fe, be).deploy(
        await project_with_build(), mode=DeployMode.seamless
    )

    assert deployment.logs_ref is not None
    log = (await get_blob_store().get(deployment.logs_ref)).decode("utf-8")
    assert "env: be project vars NOT set" in log
    assert "env: fe project vars set" in log


async def test_no_env_write_when_there_is_nothing_to_mirror() -> None:
    """A frontend deployed without a backend URL has no env — no call, not an empty one."""
    fe = FakeDeployProvider("vercel", DeployTarget.fe, _FE_URL)
    be = FakeDeployProvider("vercel", DeployTarget.be, _BE_URL)
    plan = full_plan()
    plan = plan.__class__(fe=plan.fe, be=None, db=None)

    orch = DeployOrchestrator(
        plan_source=static_plan_source(plan),
        frontend_builder=FakeFrontendBuilder(),
        backend_source=FakeBackendSource(),
        provider_factory=factory_of(fe, be),
        emitter=RecordingEmitter(),
    )
    await orch.deploy(await project_with_build(), mode=DeployMode.seamless)

    assert fe.env_calls == []
    assert fe.env_refs == []
