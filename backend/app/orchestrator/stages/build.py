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
- ``proceed`` with ``payload.approve`` — a **human approval**. No codegen, no verification: the
  person looked at the app and says it is good, and the stage goes ``complete``. The automated gate
  below is deliberately strict (typecheck, placeholder, both dev servers, every planned phase), and
  strict gates produce false negatives — a working app that trips one of them would otherwise be
  stuck out of deploy forever, because the only other route to ``complete`` is another paid build
  that may fail the same check again. The one thing an approval will not do is invent a build: it
  requires a build report to exist, so "complete" never means an untouched skeleton.

Codegen runs on its **own** ``Run`` (kind ``codegen:build``) so its token/₹ cost is a distinct,
eval-readable unit, separate from the conductor's lightweight intent trace. Budget/provider errors
raised by the agent propagate to the conductor → the API error envelope (a clear message + a
``budget.halt`` event when a cap is hit).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.agents.build_phases import NOTE_WROTE_NOTHING, PhaseOutcome
from app.agents.build_plan import PHASE_DONE
from app.agents.build_scope import BuildScope
from app.agents.codegen import BUILD_REPORT_KIND, BuildReport, CodegenAgent
from app.agents.tools.context import ToolContext
from app.core.errors import UserError
from app.db.models import Run
from app.db.models.common import utcnow
from app.db.models.enums import ArtifactType, MessageRole, Stage, StageStatus
from app.db.repos import RunRepo
from app.orchestrator.schemas import Intent, IntentAction, OutboundMessage, StageResult
from app.orchestrator.stages.build_verify import (
    BuildVerificationController,
    BuildVerifyReport,
    FeatureCodeExpectation,
)

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
        if intent.is_approval:
            return await self._approve(ctx)

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
                # phase-64: the structural check demands only the halves the plan set out to
                # build, and its hint names how many phases wrote nothing.
                expect=_expectation(report.phases),
                phases_wrote_nothing=sum(
                    1 for p in report.phases if p.note.startswith(NOTE_WROTE_NOTHING)
                ),
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

    async def _approve(self, ctx: StageContext) -> StageResult:
        """Mark the build complete on the user's say-so. No agent runs, nothing is charged."""
        project_id = ctx.project.id
        if project_id is None:  # pragma: no cover - a persisted project always carries an id
            raise UserError("Project is not persisted")

        latest = await ctx.artifacts.get_latest_of_kind(
            project_id, Stage.build, ArtifactType.code_change, BUILD_REPORT_KIND
        )
        if latest is None:
            raise UserError(
                "There is no build to approve yet — run Build first, then approve the result."
            )

        return StageResult(
            messages=[
                OutboundMessage(
                    role=MessageRole.assistant,
                    content=(
                        "Build approved by you — marked complete without re-running the agent. "
                        "Deploy is unblocked."
                    ),
                )
            ],
            next_status=StageStatus.complete,
            events=[
                {
                    "event": "progress",
                    "payload": {
                        "stage": "build",
                        "status": str(StageStatus.complete),
                        "verified": False,
                        "approved_by": "user",
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


#: Phase kinds that put code in each half of the stack (phase-56's `data|backend|frontend|wiring`).
_BACKEND_KINDS = frozenset({"data", "backend"})
_FRONTEND_KINDS = frozenset({"frontend", "wiring"})


def _expectation(phases: list[PhaseOutcome]) -> FeatureCodeExpectation:
    """Which halves the structural check may demand (phase-64).

    A planned build declares its halves through its phase kinds. Anything else — an unphased
    report, or the `small` path's single synthetic phase, which presupposes an app already built —
    expects both, since a finished app has both.
    """
    kinds = {p.kind for p in phases}
    if not kinds or not (kinds & (_BACKEND_KINDS | _FRONTEND_KINDS)):
        return FeatureCodeExpectation()
    return FeatureCodeExpectation(
        backend=bool(kinds & _BACKEND_KINDS), frontend=bool(kinds & _FRONTEND_KINDS)
    )


def _summarize(report: BuildReport, verify: BuildVerifyReport) -> str:
    lines = [report.summary or "Build complete."]
    if report.features_built:
        lines.append("Features: " + ", ".join(report.features_built))
    if report.phases:
        done = sum(1 for p in report.phases if p.status == PHASE_DONE)
        lines.append(f"Phases: {done}/{len(report.phases)} complete.")
    if not report.phases_complete:
        # phase-56's addition to the fixed stop_reason vocabulary. Each unfinished phase carries
        # its own reason (phase-64: "wrote nothing after 2 attempt(s)" is the one that matters).
        unfinished = [
            f"{p.title} ({p.note})" if p.note else p.title
            for p in report.phases
            if p.status != PHASE_DONE
        ]
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
