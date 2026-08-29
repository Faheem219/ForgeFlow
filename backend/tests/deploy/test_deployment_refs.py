"""The provider handles a deployment leaves behind (phase-62).

Everything a deployment can be asked *after* it finishes — its build log, its teardown — keys off
the provider's deployment id. That id lived only inside the orchestrator's call frame and was
discarded when it returned, which is why neither feature could exist.
"""

from __future__ import annotations

import pytest

from app.db.models.enums import DeployMode
from app.deploy.orchestrate import DeployOrchestrator
from app.deploy.providers.base import DeployState, DeployTarget
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


def _orchestrator(fe: FakeDeployProvider, be: FakeDeployProvider) -> DeployOrchestrator:
    return DeployOrchestrator(
        plan_source=static_plan_source(full_plan()),
        frontend_builder=FakeFrontendBuilder(),
        backend_source=FakeBackendSource(),
        provider_factory=factory_of(fe, be),
        emitter=RecordingEmitter(),
    )


async def test_both_targets_record_a_provider_ref() -> None:
    fe = FakeDeployProvider("vercel", DeployTarget.fe, "https://web.vercel.app")
    be = FakeDeployProvider("vercel", DeployTarget.be, "https://api.vercel.app")

    deployment = await _orchestrator(fe, be).deploy(
        await project_with_build(), mode=DeployMode.seamless
    )

    assert sorted(deployment.refs) == ["be", "fe"]
    assert deployment.refs["be"].id == "vercel-1"
    assert deployment.refs["be"].provider == "vercel"
    # The project name is what env upserts and dashboard links need — a bare id is not enough.
    assert deployment.refs["be"].project == be.deploy_specs[0].name


async def test_a_failed_target_records_no_ref() -> None:
    """Nothing was created at the provider, so there is nothing to log or destroy."""
    fe = FakeDeployProvider("vercel", DeployTarget.fe, "https://web.vercel.app")
    be = FakeDeployProvider("vercel", DeployTarget.be, "https://api.vercel.app", fail=True)

    deployment = await _orchestrator(fe, be).deploy(
        await project_with_build(), mode=DeployMode.seamless
    )

    assert deployment.refs == {}


async def test_a_backend_that_reports_failed_still_records_its_ref() -> None:
    """It *was* created — it just did not come up. Its build log is the most useful thing there is
    at that point, so the handle has to survive."""
    fe = FakeDeployProvider("vercel", DeployTarget.fe, "https://web.vercel.app")
    be = FakeDeployProvider(
        "vercel", DeployTarget.be, "https://api.vercel.app", status=DeployState.failed
    )

    deployment = await _orchestrator(fe, be).deploy(
        await project_with_build(), mode=DeployMode.seamless
    )

    assert deployment.status == "failed"
    assert deployment.refs["be"].id == "vercel-1"
    assert "fe" not in deployment.refs  # the frontend was never attempted


async def test_refs_round_trip_through_mongo() -> None:
    """They are an embedded model, not a loose dict — a reload must give them back typed."""
    from app.db.models import Deployment

    fe = FakeDeployProvider("vercel", DeployTarget.fe, "https://web.vercel.app")
    be = FakeDeployProvider("vercel", DeployTarget.be, "https://api.vercel.app")
    deployment = await _orchestrator(fe, be).deploy(
        await project_with_build(), mode=DeployMode.seamless
    )

    reloaded = await Deployment.get(deployment.id)

    assert reloaded is not None
    assert reloaded.refs["fe"].id == "vercel-1"
    assert reloaded.refs["fe"].project == fe.deploy_specs[0].name
