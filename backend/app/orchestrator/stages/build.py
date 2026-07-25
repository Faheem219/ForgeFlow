"""Build stage handler (phase-24) — wraps the codegen agent as a steerable, conversational stage.

Action mapping (§ plan phase-24 task 1; a plan-consistent resolution of the roadmap's ambiguity —
see the phase-24 Design notes):

- ``proceed`` — **build** (initial generation). Runs the codegen agent (phase-23) onto the skeleton.
  A healthy boot leaves the stage ``complete`` (so deploy's hard-prereq is satisfiable); a broken
  boot leaves it ``awaiting_user`` so it is never mistaken for deployable.
- ``refine``  — a **change request**. Runs codegen incrementally with the user's instruction;
  because the conductor resolves ``refine`` over a complete stage to the ``refine`` state action,
  downstream (test/deploy/validate) is marked ``stale`` automatically. Initial build and change
  request share ONE code path (the design note) — fewer surprises.
- ``skip``    — opt out; the stage is marked ``skipped`` and deploy stays blocked until a build
  completes (the deploy hard-prereq).

Codegen runs on its **own** ``Run`` (kind ``codegen:build``) so its token/₹ cost is a distinct,
eval-readable unit, separate from the conductor's lightweight intent trace. Budget/provider errors
raised by the agent propagate to the conductor → the API error envelope (a clear message + a
``budget.halt`` event when a cap is hit).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.agents.codegen import BuildReport, CodegenAgent
from app.core.errors import UserError
from app.db.models import Run
from app.db.models.common import utcnow
from app.db.models.enums import MessageRole, StageStatus
from app.db.repos import RunRepo
from app.orchestrator.schemas import Intent, IntentAction, OutboundMessage, StageResult

if TYPE_CHECKING:  # avoid an import cycle: stages/__init__ imports this module
    from app.orchestrator.stages.base import StageContext


class BuildStageHandler:
    def __init__(self, codegen: CodegenAgent | None = None) -> None:
        self._codegen = codegen or CodegenAgent()
        self._runs = RunRepo()

    async def handle(self, intent: Intent, ctx: StageContext) -> StageResult:
        if intent.action is IntentAction.skip:
            return StageResult(
                messages=[
                    OutboundMessage(
                        role=MessageRole.assistant,
                        content=("Build skipped — deploy stays blocked until a build completes."),
                    )
                ]
            )

        project = ctx.project
        project_id = project.id
        if project_id is None:  # pragma: no cover - a persisted project always carries an id
            raise UserError("Project is not persisted")

        # A refine carries the change-request instruction; a proceed is the initial build.
        instruction = (intent.message or "").strip() or None
        # Opt-in escape hatch: regenerate the feature code instead of extending what is on disk
        # (the default, so a failed build resumes rather than starting over).
        fresh = bool(intent.payload.get("fresh"))

        # Codegen is its own costed Run (kept distinct from the conductor's intent trace).
        run = await self._runs.insert(Run(project_id=project_id, kind="codegen:build"))
        try:
            report = await self._codegen.run(
                project,
                run,
                channel=str(project_id),
                instruction=instruction,
                fresh=fresh,
            )
        finally:
            run.finished_at = utcnow()
            await run.save()

        # A healthy boot is a real, deployable build → complete; otherwise hold for the user.
        next_status = (
            StageStatus.complete if report.boot_status == "healthy" else StageStatus.awaiting_user
        )
        return StageResult(
            messages=[OutboundMessage(role=MessageRole.assistant, content=_summarize(report))],
            next_status=next_status,
            events=[
                {
                    "event": "progress",
                    "payload": {
                        "stage": "build",
                        "status": str(next_status),
                        "boot": report.boot_status,
                        "files_changed": len(report.files_changed),
                        "run_id": str(run.id),
                    },
                }
            ],
        )


def _summarize(report: BuildReport) -> str:
    lines = [report.summary or "Build complete."]
    if report.features_built:
        lines.append("Features: " + ", ".join(report.features_built))
    tail = f"Boot: {report.boot_status} · {len(report.files_changed)} file(s) changed"
    if report.commit:
        tail += f" · commit {report.commit[:8]}"
    lines.append(tail)
    if report.follow_ups:
        lines.append("Follow-ups: " + "; ".join(report.follow_ups))
    return "\n".join(lines)
