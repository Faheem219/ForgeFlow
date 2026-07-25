"""A happy-path intent persists messages + artifacts + state and emits events (phase-08)."""

from __future__ import annotations

import pytest
from beanie import PydanticObjectId

from app.db.models.enums import MessageRole, Stage, StageStatus
from app.db.repos import RunRepo
from app.orchestrator.artifacts import ArtifactService
from app.orchestrator.conductor import Conductor
from app.orchestrator.messages import MessageService
from app.orchestrator.schemas import Intent, IntentAction
from app.projects.service import ProjectService
from app.realtime.hub import get_hub
from app.realtime.schemas import EventType

pytestmark = pytest.mark.usefixtures("mongo_db")


async def _project(user_id: PydanticObjectId) -> PydanticObjectId:
    project = await ProjectService().create_project(user_id, "p")
    assert project.id is not None
    return project.id


async def test_happy_path_persists_messages_artifacts_state_and_emits() -> None:
    user = PydanticObjectId()
    pid = await _project(user)
    hub = get_hub()

    async with hub.subscription(str(pid)) as queue:
        resp = await Conductor().handle_intent(
            user,
            Intent(
                project_id=pid, stage=Stage.test, action=IntentAction.proceed, message="go test"
            ),
        )
        events = []
        while not queue.empty():
            events.append(queue.get_nowait())

    # Inbound user message + outbound assistant message, in order.
    messages = await MessageService().list_for_project(pid)
    assert [m.role for m in messages] == [MessageRole.user, MessageRole.assistant]
    assert messages[0].content == "go test"

    # One versioned artifact, linked to the assistant reply.
    artifacts = await ArtifactService().list_for_project(pid, stage=Stage.test)
    assert len(artifacts) == 1 and artifacts[0].version == 1
    assert messages[1].artifacts == [artifacts[0].id]

    # State transition applied + reflected in the response.
    assert resp.to_status is StageStatus.complete
    stages = await ProjectService().list_stages(pid, user)
    test_state = next(s for s in stages if s.stage is Stage.test)
    assert test_state.status is StageStatus.complete
    refreshed = await ProjectService().get_owned(pid, user)
    assert refreshed.current_stage is Stage.test

    # stage.transition events emitted: the stage is published as working before the handler runs
    # (so a client watching sees it immediately, not only once the work is over), then settled.
    transition_events = [e for e in events if e.event == str(EventType.stage_transition)]
    assert [e.payload["to"] for e in transition_events] == [
        str(StageStatus.in_progress),
        str(StageStatus.complete),
    ]


async def test_run_is_opened_and_closed_with_zero_cost() -> None:
    user = PydanticObjectId()
    pid = await _project(user)

    resp = await Conductor().handle_intent(
        user, Intent(project_id=pid, stage=Stage.requirements, action=IntentAction.proceed)
    )

    runs = await RunRepo().list_for_project(pid)
    assert len(runs) == 1
    run = runs[0]
    assert str(run.id) == resp.run_id
    assert run.kind == "conductor:requirements"
    assert run.finished_at is not None  # closed
    assert run.cost.tokens == 0 and run.cost.inr == 0.0  # zero for stubs


async def test_refine_marks_downstream_stale_and_reports_it() -> None:
    user = PydanticObjectId()
    pid = await _project(user)

    await Conductor().handle_intent(
        user, Intent(project_id=pid, stage=Stage.requirements, action=IntentAction.proceed)
    )
    await Conductor().handle_intent(
        user, Intent(project_id=pid, stage=Stage.test, action=IntentAction.proceed)
    )
    resp = await Conductor().handle_intent(
        user, Intent(project_id=pid, stage=Stage.requirements, action=IntentAction.refine)
    )

    assert resp.to_status is StageStatus.in_progress
    assert resp.stale == [Stage.test]

    stages = await ProjectService().list_stages(pid, user)
    test_state = next(s for s in stages if s.stage is Stage.test)
    assert test_state.status is StageStatus.stale


async def test_artifacts_are_versioned_across_repeated_intents() -> None:
    user = PydanticObjectId()
    pid = await _project(user)

    await Conductor().handle_intent(
        user, Intent(project_id=pid, stage=Stage.requirements, action=IntentAction.proceed)
    )
    await Conductor().handle_intent(
        user, Intent(project_id=pid, stage=Stage.requirements, action=IntentAction.refine)
    )

    artifacts = await ArtifactService().list_for_project(pid, stage=Stage.requirements)
    assert [a.version for a in artifacts] == [1, 2]  # nothing overwritten
