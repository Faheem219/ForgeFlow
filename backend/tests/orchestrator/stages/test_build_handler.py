"""Build stage handler (phase-24): proceed/refine/skip over a mock codegen agent, downstream
staleness on refine, boot-gated completion, human approval, and error surfacing."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from beanie import PydanticObjectId

import app.orchestrator.stages as stages_mod
from app.agents.codegen import BUILD_REPORT_KIND, BuildReport
from app.core.config import get_config
from app.core.errors import UserError
from app.db.models import Project, Run
from app.db.models.enums import ArtifactType, MessageRole, Stage, StageStatus
from app.db.repos import StageStateRepo
from app.orchestrator.artifacts import ArtifactService
from app.orchestrator.conductor import Conductor, drain_detached
from app.orchestrator.messages import MessageService
from app.orchestrator.schemas import Intent, IntentAction
from app.orchestrator.stages import register_handler
from app.orchestrator.stages.base import StageContext
from app.orchestrator.stages.build import BuildStageHandler
from app.orchestrator.stages.build_verify import STOP_BOOT, BuildVerifyReport
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
        fresh: bool = False,
        forced_scope: object | None = None,
    ) -> BuildReport:
        self.calls.append(
            {
                "instruction": instruction,
                "channel": channel,
                "run_kind": run.kind,
                "fresh": fresh,
                # phase-60: an explicit plan-vs-incremental override off the intent payload.
                "forced_scope": forced_scope,
            }
        )
        if self._error is not None:
            raise self._error
        return self._report

    async def finalize_report(
        self,
        project_id: object,
        report: BuildReport,
        verification: dict[str, Any],
        *,
        stop_reason: str | None,
    ) -> BuildReport:
        # phase-56: the handler folds the verification verdict into the report. Nothing here needs
        # persistence — the test only cares that the gate reads the returned report.
        report.verification = verification
        report.stop_reason = report.stop_reason or stop_reason
        report.outcome = "complete" if report.stop_reason is None else "partial"
        return report


class FakeVerifier:
    """Stands in for BuildVerificationController: returns a canned verify report (phase-55)."""

    def __init__(self, report: BuildVerifyReport | None = None) -> None:
        self._report = report or BuildVerifyReport(
            ok=True,
            message="Verified.",
            typecheck_ok=True,
            placeholder_ok=True,
            fe_status="running",
            be_status="running",
        )
        self.calls: list[dict[str, Any]] = []

    async def run(
        self,
        project: Project,
        run: Run,
        *,
        ctx: object | None = None,
        channel: str | None = None,
        written_files: list[str] | None = None,
        cancel: object | None = None,
        expect: object | None = None,
        phases_wrote_nothing: int = 0,
    ) -> BuildVerifyReport:
        self.calls.append(
            {
                "written_files": written_files,
                "channel": channel,
                "expect": expect,
                "phases_wrote_nothing": phases_wrote_nothing,
            }
        )
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


async def test_proceed_runs_codegen_and_completes_when_verified() -> None:
    project = await _project()
    assert project.id is not None
    codegen = FakeCodegen(_report(boot="healthy"))
    verifier = FakeVerifier()  # ok=True

    result = await BuildStageHandler(codegen, verifier).handle(  # type: ignore[arg-type]
        Intent(project_id=project.id, stage=Stage.build, action=IntentAction.proceed),
        await _ctx(project),
    )

    assert len(codegen.calls) == 1
    assert codegen.calls[0]["instruction"] is None  # initial build, no change request
    # Verification received the files codegen wrote.
    assert verifier.calls[0]["written_files"] == ["backend/src/features/todo/todo.routes.ts"]
    assert result.next_status is StageStatus.complete
    assert result.messages[0].role is MessageRole.assistant
    assert "Built the Todos feature." in result.messages[0].content
    assert "Verified" in result.messages[0].content


async def test_failed_verification_holds_for_user() -> None:
    project = await _project()
    assert project.id is not None
    codegen = FakeCodegen(_report(boot="healthy"))  # codegen thinks it booted…
    # …but verification found a dev server down: the gate, not the boot flag, decides.
    verifier = FakeVerifier(
        BuildVerifyReport(
            ok=False,
            stop_reason=STOP_BOOT,
            message="The app did not boot cleanly — a dev server is not running.",
        )
    )

    result = await BuildStageHandler(codegen, verifier).handle(  # type: ignore[arg-type]
        Intent(project_id=project.id, stage=Stage.build, action=IntentAction.proceed),
        await _ctx(project),
    )

    assert result.next_status is StageStatus.awaiting_user  # not deployable
    assert "did not boot cleanly" in result.messages[0].content


async def test_refine_passes_change_request_and_emits_progress() -> None:
    project = await _project()
    assert project.id is not None
    codegen = FakeCodegen()

    result = await BuildStageHandler(codegen, FakeVerifier()).handle(  # type: ignore[arg-type]
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


# ------------------------------------------------------- human approval (the manual completion)


async def _record_a_build(project_id: PydanticObjectId) -> None:
    """Persist a build report, i.e. put the project in the state "a build has run"."""
    await ArtifactService().create_version(
        project_id,
        Stage.build,
        ArtifactType.code_change,
        text='{"summary": "built"}',
        meta={"kind": BUILD_REPORT_KIND},
    )


async def test_approve_completes_the_stage_without_running_codegen() -> None:
    """The human half of the completion gate: the user has looked at the app and says it is fine,
    so the stage completes with no agent run — and, crucially, nothing charged."""
    project = await _project()
    assert project.id is not None
    await _record_a_build(project.id)
    codegen = FakeCodegen()
    verifier = FakeVerifier()

    result = await BuildStageHandler(codegen, verifier).handle(  # type: ignore[arg-type]
        Intent(
            project_id=project.id,
            stage=Stage.build,
            action=IntentAction.proceed,
            payload={"approve": True},
        ),
        await _ctx(project),
    )

    assert codegen.calls == []
    assert verifier.calls == []
    assert result.next_status is StageStatus.complete
    assert "approved by you" in result.messages[0].content


async def test_approve_completes_a_build_the_verifier_had_held() -> None:
    """The case that motivates it: verification failed (or misfired), so `proceed` can never reach
    `complete` on its own and deploy stays blocked no matter how many times it is re-run."""
    project = await _project()
    assert project.id is not None
    await _record_a_build(project.id)
    held = FakeVerifier(
        BuildVerifyReport(ok=False, stop_reason=STOP_BOOT, message="A dev server is not running.")
    )

    result = await BuildStageHandler(FakeCodegen(), held).handle(  # type: ignore[arg-type]
        Intent(
            project_id=project.id,
            stage=Stage.build,
            action=IntentAction.proceed,
            payload={"approve": True},
        ),
        await _ctx(project),
    )

    assert result.next_status is StageStatus.complete


async def test_approve_rejected_when_no_build_has_ever_run() -> None:
    """Approval accepts a build; it does not invent one. Otherwise `complete` — and with it
    deploy's hard prereq — could be satisfied by an untouched skeleton."""
    project = await _project()
    assert project.id is not None
    codegen = FakeCodegen()

    with pytest.raises(UserError, match="no build to approve"):
        await BuildStageHandler(codegen, FakeVerifier()).handle(  # type: ignore[arg-type]
            Intent(
                project_id=project.id,
                stage=Stage.build,
                action=IntentAction.proceed,
                payload={"approve": True},
            ),
            await _ctx(project),
        )
    assert codegen.calls == []


async def test_plain_proceed_still_builds_when_approve_is_absent() -> None:
    """The payload flag is opt-in — an ordinary proceed must be unaffected by its existence."""
    project = await _project()
    assert project.id is not None
    codegen = FakeCodegen()

    await BuildStageHandler(codegen, FakeVerifier()).handle(  # type: ignore[arg-type]
        Intent(
            project_id=project.id,
            stage=Stage.build,
            action=IntentAction.proceed,
            payload={"approve": False},
        ),
        await _ctx(project),
    )

    assert len(codegen.calls) == 1


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
    handler = BuildStageHandler(FakeCodegen(_report(boot="healthy")), FakeVerifier())  # type: ignore[arg-type]
    register_handler(Stage.build, handler)

    # Build complete + a downstream stage complete so a refine can stale it.
    await ProjectService().transition_stage(pid, user, Stage.build, Action.complete)
    await ProjectService().transition_stage(pid, user, Stage.test, Action.complete)

    resp = await Conductor().handle_intent(
        user,
        Intent(project_id=pid, stage=Stage.build, action=IntentAction.refine, message="tweak"),
    )

    # The build is detached, so the response only acknowledges acceptance — the staling it causes
    # is applied when the background work finishes, not when the request returns.
    assert resp.to_status is StageStatus.in_progress
    assert resp.stale == []

    await drain_detached()

    stages = await ProjectService().list_stages(pid, user)
    assert next(s for s in stages if s.stage is Stage.test).status is StageStatus.stale
    # A healthy rebuild lands back at complete (deploy stays reachable).
    assert next(s for s in stages if s.stage is Stage.build).status is StageStatus.complete
