"""Taking a deployment down (phase-62).

``destroy`` existed on every adapter since phase-35 with no caller, so nothing — not even deleting
the whole project — could take a deployment down: it kept serving, and billing, with nothing in
ForgeFlow left pointing at it.

Three properties are load-bearing and each has a test here: the record is retired even when a
provider refuses, the stage state stops claiming a live deployment, and the app database is never
touched.
"""

from __future__ import annotations

import pytest
from beanie import PydanticObjectId

from app.db.models import STATUS_DELETED, Deployment, DeploymentRef
from app.db.models.enums import DeployMode, Stage, StageStatus
from app.db.repos import StageStateRepo
from app.deploy.providers.base import DeployState, DeployTarget
from app.deploy.teardown import DeploymentTeardown
from tests.deploy.orchestrate_fakes import FakeDeployProvider, factory_of

pytestmark = pytest.mark.usefixtures("mongo_db", "fernet_key", "blob_env")


async def _deployed(project_id: PydanticObjectId, *, with_refs: bool = True) -> Deployment:
    """A live deployment record, with the deploy stage complete and validate finished."""
    stages = StageStateRepo()
    await stages.set_status(project_id, Stage.deploy, StageStatus.complete)
    await stages.set_status(project_id, Stage.validate, StageStatus.complete)
    refs = (
        {
            "be": DeploymentRef(provider="vercel", id="dpl_be", project="forgeflow-api-1"),
            "fe": DeploymentRef(provider="vercel", id="dpl_fe", project="forgeflow-web-1"),
        }
        if with_refs
        else {}
    )
    return await Deployment(
        project_id=project_id,
        mode=DeployMode.seamless,
        fe_target="vercel",
        be_target="vercel",
        db_target="platform",
        urls={"be": "https://api.vercel.app", "fe": "https://web.vercel.app"},
        refs=refs,
        status="live",
        topology_snapshot={
            "status": "live",
            "nodes": [
                {"id": "fe", "kind": "frontend", "url": "https://web.vercel.app", "status": "live"},
                {"id": "be", "kind": "backend", "url": "https://api.vercel.app", "status": "live"},
            ],
            "edges": [{"source": "fe", "target": "be", "label": "VITE_API_BASE_URL"}],
        },
    ).insert()


def _providers(*, destroy_fails: bool = False) -> tuple[FakeDeployProvider, FakeDeployProvider]:
    fe = FakeDeployProvider(
        "vercel", DeployTarget.fe, "https://web.vercel.app", destroy_fails=destroy_fails
    )
    be = FakeDeployProvider(
        "vercel", DeployTarget.be, "https://api.vercel.app", destroy_fails=destroy_fails
    )
    return fe, be


async def test_every_stored_ref_is_destroyed_at_the_provider() -> None:
    pid = PydanticObjectId()
    deployment = await _deployed(pid)
    fe, be = _providers()

    report = await DeploymentTeardown(factory_of(fe, be)).destroy(deployment)

    assert sorted(report.destroyed) == ["be", "fe"]
    assert [r.id for r in be.destroy_refs] == ["dpl_be"]
    assert [r.id for r in fe.destroy_refs] == ["dpl_fe"]
    assert report.warnings == []


async def test_the_record_is_retired_and_stops_advertising_urls() -> None:
    pid = PydanticObjectId()
    deployment = await _deployed(pid)
    fe, be = _providers()

    await DeploymentTeardown(factory_of(fe, be)).destroy(deployment)

    stored = await Deployment.get(deployment.id)
    assert stored is not None
    assert stored.status == STATUS_DELETED
    assert stored.urls == {}
    assert stored.refs == {}
    assert stored.topology_snapshot["status"] == STATUS_DELETED
    assert all(node["url"] is None for node in stored.topology_snapshot["nodes"])


async def test_a_refusing_provider_is_reported_but_still_retires_the_record() -> None:
    """A deployment ForgeFlow can no longer manage is not one it should keep calling live — the
    warning says what to clean up by hand instead of blocking the delete."""
    pid = PydanticObjectId()
    deployment = await _deployed(pid)
    fe, be = _providers(destroy_fails=True)

    report = await DeploymentTeardown(factory_of(fe, be)).destroy(deployment)

    assert report.destroyed == []
    assert len(report.warnings) == 2
    assert report.record_deleted is True
    stored = await Deployment.get(deployment.id)
    assert stored is not None and stored.status == STATUS_DELETED


async def test_one_failing_target_does_not_stop_the_other() -> None:
    pid = PydanticObjectId()
    deployment = await _deployed(pid)
    fe = FakeDeployProvider("vercel", DeployTarget.fe, "https://web.vercel.app")
    be = FakeDeployProvider("vercel", DeployTarget.be, "https://api.vercel.app", destroy_fails=True)

    report = await DeploymentTeardown(factory_of(fe, be)).destroy(deployment)

    assert report.destroyed == ["fe"]
    assert report.warnings and report.warnings[0].startswith("be:")


async def test_deploy_returns_to_empty_and_validate_goes_stale() -> None:
    """``validate ⇐ deploy`` is a hard prereq: leaving deploy complete would let live-validation
    run against a URL that no longer resolves."""
    pid = PydanticObjectId()
    deployment = await _deployed(pid)
    fe, be = _providers()

    await DeploymentTeardown(factory_of(fe, be)).destroy(deployment)

    states = {s.stage: s.status for s in await StageStateRepo().list_for_project(pid)}
    assert states[Stage.deploy] is StageStatus.empty
    assert states[Stage.validate] is StageStatus.stale


async def test_a_validate_stage_that_never_ran_stays_empty() -> None:
    """Nothing to invalidate — marking it stale would invent history."""
    pid = PydanticObjectId()
    stages = StageStateRepo()
    await stages.set_status(pid, Stage.deploy, StageStatus.complete)
    deployment = await _deployed(pid)
    await stages.set_status(pid, Stage.validate, StageStatus.empty)
    fe, be = _providers()

    await DeploymentTeardown(factory_of(fe, be)).destroy(deployment)

    states = {s.stage: s.status for s in await stages.list_for_project(pid)}
    assert states[Stage.validate] is StageStatus.empty


async def test_a_record_without_refs_still_retires() -> None:
    """Deployments written before refs were persisted have nothing to destroy remotely — the record
    must still be removable, and no provider call attempted."""
    pid = PydanticObjectId()
    deployment = await _deployed(pid, with_refs=False)
    fe, be = _providers()

    report = await DeploymentTeardown(factory_of(fe, be)).destroy(deployment)

    assert report.destroyed == []
    assert report.record_deleted is True
    assert be.destroy_refs == [] and fe.destroy_refs == []


async def test_teardown_never_touches_the_database(monkeypatch: pytest.MonkeyPatch) -> None:
    """Taking a site down is not a reason to destroy what it stored (D8).

    Patched at the real seam — the module-level dropper every teardown path ends at — so this fails
    if any future change routes a database drop through the deployment teardown.
    """
    from app.deploy import db_provision

    dropped: list[str] = []

    async def spy(uri: str, db_name: str) -> None:  # pragma: no cover - must never run
        dropped.append(db_name)

    monkeypatch.setattr(db_provision, "_default_drop", spy)

    pid = PydanticObjectId()
    deployment = await _deployed(pid)
    fe, be = _providers()
    await DeploymentTeardown(factory_of(fe, be)).destroy(deployment)

    assert dropped == []


async def test_the_mode_the_deployment_used_is_what_authenticates_the_teardown() -> None:
    """A BYO deployment must be destroyed with the user's token, not the platform's — the mode is
    a property of the deployment, not of whoever is deleting it now."""
    pid = PydanticObjectId()
    deployment = await _deployed(pid)
    deployment.mode = DeployMode.byo
    await deployment.save()

    seen: list[DeployMode] = []

    class ModeSpy(FakeDeployProvider):
        async def destroy(self, ref: object, *, mode: DeployMode, user_id: object = None) -> bool:
            seen.append(mode)
            return True

    fe = ModeSpy("vercel", DeployTarget.fe, "u", status=DeployState.live)
    be = ModeSpy("vercel", DeployTarget.be, "u", status=DeployState.live)
    await DeploymentTeardown(factory_of(fe, be)).destroy(deployment)

    assert seen == [DeployMode.byo, DeployMode.byo]
