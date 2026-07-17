"""Build stage handler (phase-24): proceed/refine/skip over a mock codegen agent, downstream
staleness on refine, boot-gated completion, and error surfacing."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from beanie import PydanticObjectId

import app.orchestrator.stages as stages_mod
from app.agents.codegen import BuildReport
from app.core.config import get_config
from app.core.errors import UserError
from app.db.models import Project, Run
from app.db.models.enums import MessageRole, Stage, StageStatus
from app.db.repos import StageStateRepo
from app.orchestrator.artifacts import ArtifactService
from app.orchestrator.conductor import Conductor
from app.orchestrator.messages import MessageService
from app.orchestrator.schemas import Intent, IntentAction
from app.orchestrator.stages import register_handler
from app.orchestrator.stages.base import StageContext
from app.orchestrator.stages.build import BuildStageHandler
from app.projects.service import ProjectService
from app.projects.state_machine import Action
from app.realtime.hub import emit

pytestmark = pytest.mark.usefixtures("mongo_db")


def _report(
    *,
    boot: str = "healthy",
    follow_ups: list[str] | None = None,
    features: list[str] | None = None,
) -> BuildReport:
    return BuildReport(
        boot_status=boot,
        files_changed=["backend/src/features/todo/todo.routes.ts"],
        features_built=features if features is not None else ["Todos"],
        follow_ups=follow_ups or [],
        notes=[],
        commit="abcdef1234",
        tests_passed=True,
        summary="Built the Todos feature.",
        plan="Plan.",
    )


class FakeCodegen:
    """Records how the handler invokes codegen; returns a canned report (or raises)."""

    def __init__(self, report: BuildReport | None = None, error: Exception | None = None) -> None:
        self._report = report or _report()
        self._error = error
        self.calls: list[dict[str, Any]] = []

    async def run(
        self,
        project: Project,
        run: Run,
        *,
        ctx: object | None = None,
        channel: str | None = None,
        instruction: str | None = None,
    ) -> BuildReport:
        self.calls.append({"instruction": instruction, "channel": channel, "run_kind": run.kind})
        if self._error is not None:
            raise self._error
        return self._report


async def _project() -> Project:
    return await Project(user_id=PydanticObjectId(), name="Todo App", app_db_name="db").insert()


async def _ctx(project: Project) -> StageContext:
    assert project.id is not None
    stage_state = await StageStateRepo().get_or_create(project.id, Stage.build)
    return StageContext(
        project=project,
        stage_state=stage_state,
        messages=MessageService(),
        artifacts=ArtifactService(),
        config=get_config(),
        emit=emit,
    )


async def test_proceed_runs_codegen_and_completes_on_healthy_boot() -> None:
    project = await _project()
    assert project.id is not None
    codegen = FakeCodegen(_report(boot="healthy"))

    result = await BuildStageHandler(codegen).handle(  # type: ignore[arg-type]
        Intent(project_id=project.id, stage=Stage.build, action=IntentAction.proceed),
        await _ctx(project),
    )

    assert len(codegen.calls) == 1
    assert codegen.calls[0]["instruction"] is None  # initial build, no change request
    assert result.next_status is StageStatus.complete
    assert result.messages[0].role is MessageRole.assistant
    assert "Built the Todos feature." in result.messages[0].content


async def test_broken_boot_holds_for_user() -> None:
    project = await _project()
    assert project.id is not None
    codegen = FakeCodegen(_report(boot="unhealthy", follow_ups=["Preview is not healthy…"]))

    result = await BuildStageHandler(codegen).handle(  # type: ignore[arg-type]
        Intent(project_id=project.id, stage=Stage.build, action=IntentAction.proceed),
        await _ctx(project),
    )

    assert result.next_status is StageStatus.awaiting_user  # not deployable
    assert "Follow-ups" in result.messages[0].content


async def test_refine_passes_change_request_and_emits_progress() -> None:
    project = await _project()
    assert project.id is not None
    codegen = FakeCodegen()

    result = await BuildStageHandler(codegen).handle(  # type: ignore[arg-type]
        Intent(
            project_id=project.id,
            stage=Stage.build,
            action=IntentAction.refine,
            message="add a delete button",
        ),
        await _ctx(project),
    )

    assert codegen.calls[0]["instruction"] == "add a delete button"
    assert codegen.calls[0]["run_kind"] == "codegen:build"  # its own costed run
    assert result.events and result.events[0]["payload"]["stage"] == "build"


async def test_skip_does_not_run_codegen() -> None:
    project = await _project()
    assert project.id is not None
    codegen = FakeCodegen()

    result = await BuildStageHandler(codegen).handle(  # type: ignore[arg-type]
        Intent(project_id=project.id, stage=Stage.build, action=IntentAction.skip),
        await _ctx(project),
    )

    assert codegen.calls == []
    assert "skipped" in result.messages[0].content.lower()
    assert result.next_status is None  # the conductor maps skip → skipped


async def test_agent_errors_propagate_as_clear_messages() -> None:
    project = await _project()
    assert project.id is not None
    codegen = FakeCodegen(error=UserError("Budget cap reached (project: ₹10.00 of ₹10.00)."))

    with pytest.raises(UserError, match="Budget cap"):
        await BuildStageHandler(codegen).handle(  # type: ignore[arg-type]
            Intent(project_id=project.id, stage=Stage.build, action=IntentAction.proceed),
            await _ctx(project),
        )


@pytest.fixture
def restore_registry() -> Iterator[None]:
    snapshot = dict(stages_mod._HANDLERS)
    yield
    stages_mod._HANDLERS.clear()
    stages_mod._HANDLERS.update(snapshot)


async def test_refine_over_complete_build_marks_downstream_stale(restore_registry: None) -> None:
    user = PydanticObjectId()
    project = await ProjectService().create_project(user, "p")
    pid = project.id
    assert pid is not None
    register_handler(Stage.build, BuildStageHandler(FakeCodegen(_report(boot="healthy"))))  # type: ignore[arg-type]

    # Build complete + a downstream stage complete so a refine can stale it.
    await ProjectService().transition_stage(pid, user, Stage.build, Action.complete)
    await ProjectService().transition_stage(pid, user, Stage.test, Action.complete)

    resp = await Conductor().handle_intent(
        user,
        Intent(project_id=pid, stage=Stage.build, action=IntentAction.refine, message="tweak"),
    )

    assert Stage.test in resp.stale
    stages = await ProjectService().list_stages(pid, user)
    assert next(s for s in stages if s.stage is Stage.test).status is StageStatus.stale
    # A healthy rebuild lands back at complete (deploy stays reachable).
    assert next(s for s in stages if s.stage is Stage.build).status is StageStatus.complete
