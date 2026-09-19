"""Conductor owns transition legality — illegal intents are rejected before any side effect."""

from __future__ import annotations

import pytest
from beanie import PydanticObjectId

from app.core.errors import UserError
from app.db.models.enums import Stage, StageStatus
from app.db.repos import RunRepo
from app.orchestrator import stages
from app.orchestrator.conductor import Conductor
from app.orchestrator.messages import MessageService
from app.orchestrator.schemas import Intent, IntentAction
from app.projects.service import ProjectService
from app.projects.state_machine import Action

pytestmark = pytest.mark.usefixtures("mongo_db")


async def _project(user_id: PydanticObjectId) -> PydanticObjectId:
    project = await ProjectService().create_project(user_id, "p")
    assert project.id is not None
    return project.id


async def test_deploy_proceed_rejected_before_build_complete() -> None:
    user = PydanticObjectId()
    pid = await _project(user)

    with pytest.raises(UserError, match="build"):
        await Conductor().handle_intent(
            user, Intent(project_id=pid, stage=Stage.deploy, action=IntentAction.proceed)
        )


async def test_validate_proceed_rejected_before_deploy_complete() -> None:
    user = PydanticObjectId()
    pid = await _project(user)

    with pytest.raises(UserError, match="deploy"):
        await Conductor().handle_intent(
            user, Intent(project_id=pid, stage=Stage.validate, action=IntentAction.proceed)
        )


async def test_rejection_leaves_no_message_or_run_side_effects() -> None:
    """Illegal intents must be rejected pre-dispatch: no inbound message, no Run opened."""
    user = PydanticObjectId()
    pid = await _project(user)

    with pytest.raises(UserError):
        await Conductor().handle_intent(
            user,
            Intent(
                project_id=pid,
                stage=Stage.deploy,
                action=IntentAction.proceed,
                message="ship it",
            ),
        )

    assert await MessageService().list_for_project(pid) == []
    assert await RunRepo().list_for_project(pid) == []


async def test_deploy_proceed_allowed_after_build_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Legality only: the real deploy handler (phase-37/58) reads the workspace out of a sandbox
    # container, which made this test depend on a live Docker daemon and a built sandbox image.
    # What deploying does is covered by tests/orchestrator/stages/test_deploy_handler.py.
    monkeypatch.setitem(stages._HANDLERS, Stage.deploy, stages.StubStageHandler(Stage.deploy))
    user = PydanticObjectId()
    pid = await _project(user)

    # Mark build complete directly — build now has a real (codegen) handler, and this test only
    # cares about the deploy hard-prereq, not about running a build.
    await ProjectService().transition_stage(pid, user, Stage.build, Action.complete)
    resp = await Conductor().handle_intent(
        user, Intent(project_id=pid, stage=Stage.deploy, action=IntentAction.proceed)
    )
    assert resp.to_status is StageStatus.complete


async def test_skip_is_never_blocked_by_hard_prereqs() -> None:
    user = PydanticObjectId()
    pid = await _project(user)

    resp = await Conductor().handle_intent(
        user, Intent(project_id=pid, stage=Stage.deploy, action=IntentAction.skip)
    )
    assert resp.to_status is StageStatus.skipped


async def test_refine_on_empty_stage_enters_it() -> None:
    user = PydanticObjectId()
    pid = await _project(user)

    # requirements is still a stub, so this isolates the conductor's action resolution
    # (refine on an empty stage → enter → in_progress) from any real handler behavior.
    resp = await Conductor().handle_intent(
        user, Intent(project_id=pid, stage=Stage.requirements, action=IntentAction.refine)
    )
    assert resp.from_status is StageStatus.empty
    assert resp.to_status is StageStatus.in_progress
    assert resp.stale == []


async def test_intent_on_foreign_project_is_not_found() -> None:
    from app.core.errors import NotFoundError

    owner, intruder = PydanticObjectId(), PydanticObjectId()
    pid = await _project(owner)

    with pytest.raises(NotFoundError):
        await Conductor().handle_intent(
            intruder, Intent(project_id=pid, stage=Stage.design, action=IntentAction.proceed)
        )
