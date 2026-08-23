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

from app.agents.build_plan import PHASE_DONE
from app.agents.build_scope import BuildScope
from app.agents.codegen import BuildReport, CodegenAgent
from app.agents.tools.context import ToolContext
from app.core.errors import UserError
from app.db.models import Run
from app.db.models.common import utcnow
from app.db.models.enums import MessageRole, StageStatus
from app.db.repos import RunRepo
from app.orchestrator.schemas import Intent, IntentAction, OutboundMessage, StageResult
from app.orchestrator.stages.build_verify import BuildVerificationController, BuildVerifyReport

#: phase-56's addition to phase-55's fixed stop_reason vocabulary.
STOP_PHASES = "phases_incomplete"

if TYPE_CHECKING:  # avoid an import cycle: stages/__init__ imports this module
    from app.orchestrator.stages.base import StageContext


class BuildStageHandler:
    def __init__(
        self,
        codegen: CodegenAgent | None = None,
        verifier: BuildVerificationController | None = None,
    ) -> None:
        self._codegen = codegen or CodegenAgent()
        self._verifier = verifier or BuildVerificationController()
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
        # phase-60: the scope classifier decides plan-vs-incremental, and this overrides it when it
        # gets one wrong — a misclassification should cost a click, not a dead end.
        forced_scope = _forced_scope(intent.payload.get("scope"))

        # Codegen + verification share ONE costed Run (kept distinct from the conductor's intent
        # trace) and ONE ToolContext, so budget/cost accounting and the concurrent-build lock cover
        # the whole build, not just generation.
        channel = str(project_id)
        run = await self._runs.insert(Run(project_id=project_id, kind="codegen:build"))
        tool_ctx = ToolContext.build(project, run, channel=channel)
        try:
            report = await self._codegen.run(
                project,
                run,
                ctx=tool_ctx,
                channel=channel,
                instruction=instruction,
                fresh=fresh,
                forced_scope=forced_scope,
            )
            # Verify for real (install → typecheck → boot → placeholder) and self-heal code-class
            # failures through the bounded repair loop; env failures short-circuit, zero repairs.
            verify = await self._verifier.run(
                project,
                run,
                ctx=tool_ctx,
                channel=channel,
                written_files=report.files_changed,
            )
        finally:
            run.finished_at = utcnow()
            await run.save()

        # `complete` now requires a *working* app: typecheck clean, no placeholder, both dev servers
        # healthy (the verification gate) — not merely a boot — AND every planned phase done
        # (phase-56). Anything else holds for the user.
        phases_ok = report.phases_complete
        stop_reason = verify.stop_reason or (None if phases_ok else STOP_PHASES)
        ok = verify.ok and phases_ok
        report = await self._codegen.finalize_report(
            project_id, report, verify.to_dict(), stop_reason=stop_reason
        )
        next_status = StageStatus.complete if ok else StageStatus.awaiting_user
        return StageResult(
            messages=[
                OutboundMessage(role=MessageRole.assistant, content=_summarize(report, verify))
            ],
            next_status=next_status,
            events=[
                {
                    "event": "progress",
                    "payload": {
                        "stage": "build",
                        "status": str(next_status),
                        "boot": report.boot_status,
                        "verified": ok,
                        "stop_reason": stop_reason,
                        "files_changed": len(report.files_changed),
                        "run_id": str(run.id),
                    },
                }
            ],
        )


def _forced_scope(value: object) -> BuildScope | None:
    """Read an explicit scope override off the intent payload; anything unrecognised is ignored."""
    if not isinstance(value, str):
        return None
    try:
        return BuildScope(value.strip().lower())
    except ValueError:
        return None


def _summarize(report: BuildReport, verify: BuildVerifyReport) -> str:
    lines = [report.summary or "Build complete."]
    if report.features_built:
        lines.append("Features: " + ", ".join(report.features_built))
    if report.phases:
        done = sum(1 for p in report.phases if p.status == PHASE_DONE)
        lines.append(f"Phases: {done}/{len(report.phases)} complete.")
    if not report.phases_complete:
        # phase-56's addition to the fixed stop_reason vocabulary.
        unfinished = [p.title for p in report.phases if p.status != PHASE_DONE]
        lines.append(
            "Some planned phases did not finish: " + ", ".join(unfinished) + ". Run Build again "
            "to continue from the first unfinished one."
        )
    if verify.ok:
        lines.append(
            "Verified: typecheck clean, both dev servers healthy, no template placeholder left."
            + (" (self-healed during the build)" if verify.repaired else "")
        )
    else:
        # One precise sentence per stop_reason (from the verification report), plus its fix hint.
        lines.append(verify.message)
        if verify.hint:
            lines.append(verify.hint)
    tail = f"{len(report.files_changed)} file(s) changed"
    if report.commit:
        tail += f" · commit {report.commit[:8]}"
    lines.append(tail)
    return "\n".join(lines)
