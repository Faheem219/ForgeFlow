"""Undoing a skip, and approving a stage by hand — the two ways a human overrides the agent.

Both exist because the automated paths are one-way: `skip` used to be undoable only by re-running
the stage (a paid rebuild, for build), and `complete` on build was reachable only through the
verification gate. Neither should cost an agent run.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from beanie import PydanticObjectId

import app.orchestrator.stages as stages_mod
from app.core.errors import UserError
from app.db.models.enums import ArtifactType, MessageRole, Stage, StageStatus
from app.db.repos import RunRepo, StageStateRepo
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
    """A stage handler that records every dispatch. An un-skip must never reach one."""

    def __init__(self) -> None:
        self.calls: list[IntentAction] = []

    async def handle(self, intent: Intent, ctx: StageContext) -> StageResult:
        self.calls.append(intent.action)
        return StageResult()


@pytest.fixture
def restore_registry() -> Iterator[None]:
    snapshot = dict(stages_mod._HANDLERS)
    yield
    stages_mod._HANDLERS.clear()
    stages_mod._HANDLERS.update(snapshot)


async def _project(user_id: PydanticObjectId) -> PydanticObjectId:
    project = await ProjectService().create_project(user_id, "p")
    assert project.id is not None
    return project.id


async def _status(project_id: PydanticObjectId, stage: Stage) -> StageStatus:
    states = await StageStateRepo().list_for_project(project_id)
    return next(s.status for s in states if s.stage is stage)


# ------------------------------------------------------------------------------ undoing a skip


async def test_unskip_restores_a_build_that_was_complete(restore_registry: None) -> None:
    """The accident this is for: an already-built, verified app skipped by mistake. Un-skipping
    puts `complete` back, so deploy is unblocked again without regenerating anything."""
    user = PydanticObjectId()
    pid = await _project(user)
    handler = SpyHandler()
    register_handler(Stage.build, handler)

    await ProjectService().transition_stage(pid, user, Stage.build, Action.complete)
    await Conductor().handle_intent(
        user, Intent(project_id=pid, stage=Stage.build, action=IntentAction.skip)
    )
    assert await _status(pid, Stage.build) is StageStatus.skipped

    resp = await Conductor().handle_intent(
        user, Intent(project_id=pid, stage=Stage.build, action=IntentAction.unskip)
    )

    assert resp.to_status is StageStatus.complete
    assert await _status(pid, Stage.build) is StageStatus.complete
    # The undo cost nothing: no codegen, no verification, no handler at all.
    assert handler.calls == [IntentAction.skip]


async def test_unskip_leaves_deploy_deployable_again(restore_registry: None) -> None:
    """Restoring `complete` restores the hard prereq it satisfied — the skip's real damage."""
    user = PydanticObjectId()
    pid = await _project(user)
    register_handler(Stage.build, SpyHandler())
    register_handler(Stage.deploy, SpyHandler())

    await ProjectService().transition_stage(pid, user, Stage.build, Action.complete)
    await Conductor().handle_intent(
        user, Intent(project_id=pid, stage=Stage.build, action=IntentAction.skip)
    )
    with pytest.raises(UserError, match="build"):
        await Conductor().handle_intent(
            user, Intent(project_id=pid, stage=Stage.deploy, action=IntentAction.proceed)
        )

    await Conductor().handle_intent(
        user, Intent(project_id=pid, stage=Stage.build, action=IntentAction.unskip)
    )

    resp = await Conductor().handle_intent(
        user, Intent(project_id=pid, stage=Stage.deploy, action=IntentAction.proceed)
    )
    assert resp.to_status is StageStatus.complete


async def test_unskip_of_a_never_started_stage_returns_it_to_empty(restore_registry: None) -> None:
    user = PydanticObjectId()
    pid = await _project(user)
    register_handler(Stage.design, SpyHandler())

    await Conductor().handle_intent(
        user, Intent(project_id=pid, stage=Stage.design, action=IntentAction.skip)
    )
    resp = await Conductor().handle_intent(
        user, Intent(project_id=pid, stage=Stage.design, action=IntentAction.unskip)
    )

    assert resp.to_status is StageStatus.empty


async def test_unskip_rejected_when_the_stage_is_not_skipped() -> None:
    user = PydanticObjectId()
    pid = await _project(user)

    with pytest.raises(UserError, match="skipped"):
        await Conductor().handle_intent(
            user, Intent(project_id=pid, stage=Stage.build, action=IntentAction.unskip)
        )


async def test_unskip_records_what_it_restored_in_the_conversation(
    restore_registry: None,
) -> None:
    """The conversation is the project's durable record — an undo has to show up in it."""
    user = PydanticObjectId()
    pid = await _project(user)
    register_handler(Stage.build, SpyHandler())

    await ProjectService().transition_stage(pid, user, Stage.build, Action.complete)
    await Conductor().handle_intent(
        user, Intent(project_id=pid, stage=Stage.build, action=IntentAction.skip)
    )
    await Conductor().handle_intent(
        user, Intent(project_id=pid, stage=Stage.build, action=IntentAction.unskip)
    )

    messages = await MessageService().list_for_project(pid, stage=Stage.build)
    reply = messages[-1]
    assert reply.role is MessageRole.assistant
    assert "no longer skipped" in reply.content
    assert "complete" in reply.content


async def test_unskip_does_not_hold_the_build_lock(restore_registry: None) -> None:
    """It is not a build, so it must not open one — a stuck lock would block every later build."""
    user = PydanticObjectId()
    pid = await _project(user)
    register_handler(Stage.build, SpyHandler())

    await Conductor().handle_intent(
        user, Intent(project_id=pid, stage=Stage.build, action=IntentAction.skip)
    )
    await Conductor().handle_intent(
        user, Intent(project_id=pid, stage=Stage.build, action=IntentAction.unskip)
    )
    await drain_detached()

    assert await RunRepo().active_for_project(pid, BUILD_RUN_KINDS) == []


# -------------------------------------------------------------------------- approving by hand


async def test_approved_build_completes_synchronously(restore_registry: None) -> None:
    """An approval does no work, so it must answer with the real outcome rather than being
    detached like a build — the UI has nothing to follow on the realtime channel."""
    user = PydanticObjectId()
    pid = await _project(user)
    register_handler(Stage.build, SpyHandler())
    await ArtifactService().create_version(
        pid,
        Stage.build,
        ArtifactType.code_change,
        text="{}",
        meta={"kind": "build_report"},
    )

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
    assert await _status(pid, Stage.build) is StageStatus.complete


async def test_an_approval_is_legal_over_a_skipped_build(restore_registry: None) -> None:
    """A user who skipped, then decided the code on disk is fine, can complete it directly."""
    user = PydanticObjectId()
    pid = await _project(user)
    register_handler(Stage.build, SpyHandler())

    await Conductor().handle_intent(
        user, Intent(project_id=pid, stage=Stage.build, action=IntentAction.skip)
    )
    resp = await Conductor().handle_intent(
        user,
        Intent(
            project_id=pid,
            stage=Stage.build,
            action=IntentAction.proceed,
            payload={"approve": True},
        ),
    )

    assert resp.from_status is StageStatus.skipped
    assert resp.to_status is StageStatus.complete
