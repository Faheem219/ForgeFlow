"""Resetting a finished stage (user-requested 2026-10-02) — taking back a "done" without re-running.

The case it exists for: a demo project kept built up to Build and shown again and again. Its later
stages have to look undone before each showing. Build and Test go back to "Needs you" with every
artifact kept; Deploy goes back to a blank slate, which means the live deployment is actually taken
down — the next demo shows a fresh one — and Validate (whose evidence was about that deployment)
goes blank with it.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from beanie import PydanticObjectId

import app.orchestrator.stages as stages_mod
from app.core.errors import UserError
from app.db.models import STATUS_DELETED, Deployment, DeploymentRef, Run
from app.db.models.enums import ArtifactType, DeployMode, MessageRole, Stage, StageStatus
from app.db.repos import RunRepo, StageStateRepo
from app.deploy.providers.base import DeployRef
from app.orchestrator.artifacts import ArtifactService
from app.orchestrator.conductor import BUILD_RUN_KINDS, Conductor, drain_detached
from app.orchestrator.messages import MessageService
from app.orchestrator.schemas import Intent, IntentAction, StageResult
from app.orchestrator.stages import register_handler
from app.orchestrator.stages.base import StageContext
from app.projects.service import ProjectService
from app.projects.state_machine import Action

pytestmark = pytest.mark.usefixtures("mongo_db")


class SpyHandler:
    """Records every dispatch. A reset must never reach a stage handler."""

    def __init__(self) -> None:
        self.calls: list[IntentAction] = []

    async def handle(self, intent: Intent, ctx: StageContext) -> StageResult:
        self.calls.append(intent.action)
        return StageResult()


class StubProvider:
    """Records teardown calls — installed over the teardown service's provider lookup."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.destroyed: list[DeployRef] = []

    async def destroy(self, ref: DeployRef, **_: Any) -> bool:
        self.destroyed.append(ref)
        if self.error is not None:
            raise self.error
        return True


@pytest.fixture
def restore_registry() -> Iterator[None]:
    snapshot = dict(stages_mod._HANDLERS)
    yield
    stages_mod._HANDLERS.clear()
    stages_mod._HANDLERS.update(snapshot)


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> StubProvider:
    stub = StubProvider()
    from app.deploy import teardown as teardown_mod

    monkeypatch.setattr(teardown_mod, "provider_for", lambda _target: stub)
    return stub


async def _project(user_id: PydanticObjectId) -> PydanticObjectId:
    project = await ProjectService().create_project(user_id, "demo")
    assert project.id is not None
    return project.id


async def _status(project_id: PydanticObjectId, stage: Stage) -> StageStatus:
    states = await StageStateRepo().list_for_project(project_id)
    return next(s.status for s in states if s.stage is stage)


async def _all_done(user: PydanticObjectId, pid: PydanticObjectId) -> None:
    """Every stage complete, in pipeline order so the hard prereqs are satisfied."""
    for stage in (
        Stage.requirements,
        Stage.design,
        Stage.build,
        Stage.test,
        Stage.deploy,
        Stage.validate,
    ):
        await ProjectService().transition_stage(pid, user, stage, Action.complete)


async def _deployment(pid: PydanticObjectId) -> Deployment:
    return await Deployment(
        project_id=pid,
        mode=DeployMode.seamless,
        fe_target="vercel",
        be_target="vercel",
        db_target="platform",
        urls={"fe": "https://web", "be": "https://api"},
        refs={
            "be": DeploymentRef(provider="vercel", id="dpl_be", project="api"),
            "fe": DeploymentRef(provider="vercel", id="dpl_fe", project="web"),
        },
        status="live",
    ).insert()


async def _reset(user: PydanticObjectId, pid: PydanticObjectId, stage: Stage) -> Any:
    return await Conductor().handle_intent(
        user, Intent(project_id=pid, stage=stage, action=IntentAction.reset)
    )


# --------------------------------------------------------------------- build / test: status only


async def test_resetting_a_complete_build_marks_it_needs_you_and_keeps_the_work(
    restore_registry: None,
) -> None:
    user = PydanticObjectId()
    pid = await _project(user)
    handler = SpyHandler()
    register_handler(Stage.build, handler)
    report = await ArtifactService().create_version(
        pid, Stage.build, ArtifactType.code_change, text="{}", meta={"kind": "build_report"}
    )
    await _all_done(user, pid)

    resp = await _reset(user, pid, Stage.build)

    assert resp.from_status is StageStatus.complete
    assert resp.to_status is StageStatus.awaiting_user
    assert await _status(pid, Stage.build) is StageStatus.awaiting_user
    assert handler.calls == []  # nothing re-ran
    # Nothing was deleted: the build report is still the latest one.
    latest = await ArtifactService().get_latest_of_kind(
        pid, Stage.build, ArtifactType.code_change, "build_report"
    )
    assert latest is not None and latest.id == report.id
    # Only Build moved — the later stages a demo shows next are untouched.
    for stage in (Stage.test, Stage.deploy, Stage.validate):
        assert await _status(pid, stage) is StageStatus.complete


async def test_a_reset_build_can_be_approved_back_to_complete(restore_registry: None) -> None:
    """The way back costs a click, not a rebuild."""
    user = PydanticObjectId()
    pid = await _project(user)
    register_handler(Stage.build, SpyHandler())
    await ProjectService().transition_stage(pid, user, Stage.build, Action.complete)
    await _reset(user, pid, Stage.build)

    resp = await Conductor().handle_intent(
        user,
        Intent(
            project_id=pid,
            stage=Stage.build,
            action=IntentAction.proceed,
            payload={"approve": True},
        ),
    )

    assert resp.to_status is StageStatus.complete


async def test_resetting_test_marks_it_needs_you(restore_registry: None) -> None:
    user = PydanticObjectId()
    pid = await _project(user)
    handler = SpyHandler()
    register_handler(Stage.test, handler)
    await _all_done(user, pid)

    resp = await _reset(user, pid, Stage.test)

    assert resp.to_status is StageStatus.awaiting_user
    assert handler.calls == []
    assert await _status(pid, Stage.deploy) is StageStatus.complete


async def test_the_reset_is_recorded_with_the_way_back() -> None:
    user = PydanticObjectId()
    pid = await _project(user)
    await ProjectService().transition_stage(pid, user, Stage.build, Action.complete)

    await _reset(user, pid, Stage.build)

    messages = await MessageService().list_for_project(pid, stage=Stage.build)
    reply = messages[-1]
    assert reply.role is MessageRole.assistant
    assert "needs you" in reply.content
    assert "Approve" in reply.content


async def test_a_status_only_reset_needs_a_complete_stage() -> None:
    user = PydanticObjectId()
    pid = await _project(user)

    with pytest.raises(UserError, match="complete"):
        await _reset(user, pid, Stage.build)


async def test_a_reset_is_refused_while_the_stage_is_still_working() -> None:
    """A refine leaves build `complete` while the agent works; a reset then would be overwritten
    the moment that build finished."""
    user = PydanticObjectId()
    pid = await _project(user)
    await ProjectService().transition_stage(pid, user, Stage.build, Action.complete)
    await RunRepo().insert(Run(project_id=pid, kind="codegen:build"))

    with pytest.raises(UserError, match="still running"):
        await _reset(user, pid, Stage.build)
    assert await _status(pid, Stage.build) is StageStatus.complete


async def test_a_reset_does_not_hold_the_build_lock() -> None:
    user = PydanticObjectId()
    pid = await _project(user)
    await ProjectService().transition_stage(pid, user, Stage.build, Action.complete)

    await _reset(user, pid, Stage.build)
    await drain_detached()

    assert await RunRepo().active_for_project(pid, BUILD_RUN_KINDS) == []


# ---------------------------------------------------------------------- deploy: a blank slate


async def test_resetting_deploy_takes_the_deployment_down_and_blanks_validate(
    provider: StubProvider,
) -> None:
    user = PydanticObjectId()
    pid = await _project(user)
    await _all_done(user, pid)
    deployment = await _deployment(pid)

    resp = await _reset(user, pid, Stage.deploy)

    assert resp.to_status is StageStatus.empty
    assert resp.cleared == [Stage.deploy, Stage.validate]
    assert await _status(pid, Stage.deploy) is StageStatus.empty
    assert await _status(pid, Stage.validate) is StageStatus.empty
    # The build the next demo deploys from is untouched.
    assert await _status(pid, Stage.build) is StageStatus.complete
    # Really taken down at the provider, and the record retired — kept, not deleted (§7).
    assert sorted(ref.id for ref in provider.destroyed) == ["dpl_be", "dpl_fe"]
    record = await Deployment.get(deployment.id)
    assert record is not None and record.status == STATUS_DELETED
    # Both stages remember when their earlier results stopped being current.
    repo = StageStateRepo()
    assert await repo.cleared_at(pid, Stage.deploy) is not None
    assert await repo.cleared_at(pid, Stage.validate) is not None


async def test_a_deploy_reset_reports_what_the_provider_refused(provider: StubProvider) -> None:
    """Fail soft, report loudly: the stage still resets, and the leftovers are named."""
    provider.error = RuntimeError("401 unauthorized")
    user = PydanticObjectId()
    pid = await _project(user)
    await _all_done(user, pid)
    await _deployment(pid)

    resp = await _reset(user, pid, Stage.deploy)

    assert resp.to_status is StageStatus.empty
    assert "401 unauthorized" in resp.messages[0].content


async def test_a_deploy_can_be_reset_with_no_deployment_left(provider: StubProvider) -> None:
    user = PydanticObjectId()
    pid = await _project(user)
    await _all_done(user, pid)

    resp = await _reset(user, pid, Stage.deploy)

    assert resp.to_status is StageStatus.empty
    assert provider.destroyed == []
    assert "no live deployment" in resp.messages[0].content


async def test_a_deploy_reset_is_refused_while_validation_runs(provider: StubProvider) -> None:
    """It would remove the deployment the live tests are running against."""
    user = PydanticObjectId()
    pid = await _project(user)
    await _all_done(user, pid)
    await _deployment(pid)
    await RunRepo().insert(Run(project_id=pid, kind="validate"))

    with pytest.raises(UserError, match="still running"):
        await _reset(user, pid, Stage.deploy)
    assert provider.destroyed == []


async def test_resetting_validate_alone_keeps_the_deployment_live(provider: StubProvider) -> None:
    user = PydanticObjectId()
    pid = await _project(user)
    await _all_done(user, pid)
    deployment = await _deployment(pid)

    resp = await _reset(user, pid, Stage.validate)

    assert resp.to_status is StageStatus.empty
    assert resp.cleared == [Stage.validate]
    assert await _status(pid, Stage.deploy) is StageStatus.complete
    assert provider.destroyed == []
    record = await Deployment.get(deployment.id)
    assert record is not None and record.status == "live"


async def test_a_reset_deploy_can_be_deployed_again() -> None:
    """Blank means blank: the hard prereq (build complete) is all a fresh deploy needs."""
    user = PydanticObjectId()
    pid = await _project(user)
    await _all_done(user, pid)
    await _reset(user, pid, Stage.deploy)

    outcome = await ProjectService().transition_stage(pid, user, Stage.deploy, Action.complete)

    assert outcome.to_status is StageStatus.complete
