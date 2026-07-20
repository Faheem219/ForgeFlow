"""Deploy stage handler (phase-37): proceed/refine run orchestration, skip opts out, completion is
gated on a live result, and the deploy mode is resolved from the intent/config."""

from __future__ import annotations

from typing import Any

import pytest
from beanie import PydanticObjectId

from app.core.config import get_config
from app.db.models import Deployment, Project
from app.db.models.enums import DeployMode, MessageRole, Stage, StageStatus
from app.db.repos import StageStateRepo
from app.orchestrator.artifacts import ArtifactService
from app.orchestrator.messages import MessageService
from app.orchestrator.schemas import Intent, IntentAction
from app.orchestrator.stages.base import StageContext
from app.orchestrator.stages.deploy import DeployStageHandler
from app.realtime.hub import emit

pytestmark = pytest.mark.usefixtures("mongo_db")


class FakeOrchestrator:
    def __init__(self, status: str = "live") -> None:
        self._status = status
        self.calls: list[dict[str, Any]] = []

    async def deploy(
        self, project: Project, *, mode: DeployMode, user_id: PydanticObjectId | None = None
    ) -> Deployment:
        self.calls.append({"mode": mode, "user_id": user_id})
        return Deployment(
            project_id=project.id,
            mode=mode,
            status=self._status,
            urls={"fe": "https://web.vercel.app", "be": "https://api.onrender.com"},
        )


async def _project() -> Project:
    return await Project(user_id=PydanticObjectId(), name="app", app_db_name="db").insert()


async def _ctx(project: Project) -> StageContext:
    assert project.id is not None
    stage_state = await StageStateRepo().get_or_create(project.id, Stage.deploy)
    return StageContext(
        project=project,
        stage_state=stage_state,
        messages=MessageService(),
        artifacts=ArtifactService(),
        config=get_config(),
        emit=emit,
    )


async def test_proceed_deploys_and_completes_when_live() -> None:
    project = await _project()
    assert project.id is not None
    orch = FakeOrchestrator(status="live")

    result = await DeployStageHandler(orch).handle(  # type: ignore[arg-type]
        Intent(project_id=project.id, stage=Stage.deploy, action=IntentAction.proceed),
        await _ctx(project),
    )

    assert len(orch.calls) == 1
    assert orch.calls[0]["mode"] is DeployMode.seamless  # from config default
    assert orch.calls[0]["user_id"] == project.user_id
    assert result.next_status is StageStatus.complete
    assert "https://web.vercel.app" in result.messages[0].content
    assert result.messages[0].role is MessageRole.assistant


async def test_degraded_deploy_holds_for_user() -> None:
    project = await _project()
    assert project.id is not None

    result = await DeployStageHandler(FakeOrchestrator(status="degraded")).handle(  # type: ignore[arg-type]
        Intent(project_id=project.id, stage=Stage.deploy, action=IntentAction.proceed),
        await _ctx(project),
    )
    assert result.next_status is StageStatus.awaiting_user  # not a healthy deploy


async def test_skip_does_not_deploy() -> None:
    project = await _project()
    assert project.id is not None
    orch = FakeOrchestrator()

    result = await DeployStageHandler(orch).handle(  # type: ignore[arg-type]
        Intent(project_id=project.id, stage=Stage.deploy, action=IntentAction.skip),
        await _ctx(project),
    )
    assert orch.calls == []
    assert "skipped" in result.messages[0].content.lower()
    assert result.next_status is None


async def test_byo_mode_from_intent_payload() -> None:
    project = await _project()
    assert project.id is not None
    orch = FakeOrchestrator()

    await DeployStageHandler(orch).handle(  # type: ignore[arg-type]
        Intent(
            project_id=project.id,
            stage=Stage.deploy,
            action=IntentAction.proceed,
            payload={"mode": "byo"},
        ),
        await _ctx(project),
    )
    assert orch.calls[0]["mode"] is DeployMode.byo
