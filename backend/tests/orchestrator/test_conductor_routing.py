"""Conductor routes intents to the correct stage handler (phase-08)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from beanie import PydanticObjectId

import app.orchestrator.stages as stages_mod
from app.core.errors import SystemError  # noqa: A004 - taxonomy name fixed by the plan
from app.db.models.enums import ArtifactType, MessageRole, Stage
from app.orchestrator.conductor import Conductor
from app.orchestrator.schemas import (
    ArtifactSpec,
    Intent,
    IntentAction,
    OutboundMessage,
    StageResult,
)
from app.orchestrator.stages import get_handler, register_handler
from app.orchestrator.stages.base import StageContext
from app.projects.service import ProjectService

pytestmark = pytest.mark.usefixtures("mongo_db")


@pytest.fixture
def restore_registry() -> Iterator[None]:
    """Snapshot + restore the module-global handler registry around a test that mutates it."""
    snapshot = dict(stages_mod._HANDLERS)
    yield
    stages_mod._HANDLERS.clear()
    stages_mod._HANDLERS.update(snapshot)


async def _project(user_id: PydanticObjectId) -> PydanticObjectId:
    project = await ProjectService().create_project(user_id, "p")
    assert project.id is not None
    return project.id


async def test_each_stage_routes_to_its_own_stub_handler() -> None:
    user = PydanticObjectId()
    pid = await _project(user)

    # The stub for each stage emits that stage's characteristic artifact type — proof of routing.
    # (design (phase-19) and build (phase-24) have real handlers now; the still-stubbed stages
    # prove routing here.)
    expected = {
        Stage.requirements: ArtifactType.requirement,
        Stage.test: ArtifactType.test,
    }
    for stage, artifact_type in expected.items():
        resp = await Conductor().handle_intent(
            user, Intent(project_id=pid, stage=stage, action=IntentAction.proceed)
        )
        assert resp.stage is stage
        assert [a.type for a in resp.artifacts] == [artifact_type]


async def test_register_handler_overrides_stub(restore_registry: None) -> None:
    class SpyHandler:
        async def handle(self, intent: Intent, ctx: StageContext) -> StageResult:
            return StageResult(
                messages=[OutboundMessage(role=MessageRole.assistant, content="spied!")],
                artifacts=[ArtifactSpec(stage=Stage.design, type=ArtifactType.design, text="x")],
            )

    register_handler(Stage.design, SpyHandler())

    user = PydanticObjectId()
    pid = await _project(user)
    resp = await Conductor().handle_intent(
        user, Intent(project_id=pid, stage=Stage.design, action=IntentAction.proceed)
    )
    assert resp.messages[0].content == "spied!"


async def test_every_stage_has_a_registered_handler() -> None:
    for stage in Stage:
        assert get_handler(stage) is not None


async def test_get_handler_raises_for_unregistered_stage(restore_registry: None) -> None:
    del stages_mod._HANDLERS[Stage.deploy]
    with pytest.raises(SystemError):
        get_handler(Stage.deploy)
