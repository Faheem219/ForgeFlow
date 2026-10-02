from __future__ import annotations

import pytest
from beanie import PydanticObjectId

from app.db.models.enums import Stage, StageStatus
from app.projects.service import ProjectService
from app.projects.state_machine import Action
from app.realtime.hub import get_hub
from app.realtime.schemas import EventType

pytestmark = pytest.mark.usefixtures("mongo_db")


async def test_transition_emits_stage_transition_event_with_correct_payload() -> None:
    owner = PydanticObjectId()
    project = await ProjectService().create_project(owner, "p")
    assert project.id is not None
    hub = get_hub()

    async with hub.subscription(str(project.id)) as queue:
        await ProjectService().transition_stage(project.id, owner, Stage.design, Action.complete)
        event = await queue.get()

    assert event.event == str(EventType.stage_transition)
    assert event.project_id == str(project.id)
    assert event.stage is Stage.design
    assert event.payload == {
        "stage": "design",
        "from": str(StageStatus.empty),
        "to": str(StageStatus.complete),
        "stale": [],
    }


async def test_refine_event_lists_stale_stages_in_payload() -> None:
    owner = PydanticObjectId()
    project = await ProjectService().create_project(owner, "p")
    assert project.id is not None
    hub = get_hub()

    await ProjectService().transition_stage(project.id, owner, Stage.requirements, Action.complete)
    await ProjectService().transition_stage(project.id, owner, Stage.design, Action.complete)

    async with hub.subscription(str(project.id)) as queue:
        await ProjectService().transition_stage(
            project.id, owner, Stage.requirements, Action.refine
        )
        event = await queue.get()

    assert event.payload["stale"] == ["design"]


async def test_every_transition_action_emits_exactly_one_event() -> None:
    owner = PydanticObjectId()
    project = await ProjectService().create_project(owner, "p")
    assert project.id is not None
    hub = get_hub()

    async with hub.subscription(str(project.id)) as queue:
        await ProjectService().transition_stage(project.id, owner, Stage.build, Action.enter)
        first = await queue.get()
        await ProjectService().transition_stage(project.id, owner, Stage.build, Action.skip)
        second = await queue.get()

    assert first.seq == 1
    assert second.seq == 2


async def test_a_deploy_reset_names_the_stages_it_cleared() -> None:
    owner = PydanticObjectId()
    project = await ProjectService().create_project(owner, "p")
    assert project.id is not None
    for stage in (Stage.build, Stage.deploy, Stage.validate):
        await ProjectService().transition_stage(project.id, owner, stage, Action.complete)
    hub = get_hub()

    async with hub.subscription(str(project.id)) as queue:
        await ProjectService().transition_stage(project.id, owner, Stage.deploy, Action.reset)
        first = await queue.get()
        second = await queue.get()

    assert first.payload["cleared"] == ["deploy", "validate"]
    assert first.payload["to"] == str(StageStatus.empty)
    # Validate's own transition, so every viewer's navigator refreshes it too.
    assert second.stage is Stage.validate
    assert second.payload == {
        "stage": "validate",
        "from": str(StageStatus.complete),
        "to": str(StageStatus.empty),
    }
