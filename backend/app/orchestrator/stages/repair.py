"""Bounded, convergent repair-loop controller (phase-31, §9 steps 4–5).

The defensible contribution (D1/D7). Three invariants are absolute here:

    never loop unbounded · always leave an auditable trail · prefer escalation over silent failure

Each iteration is: assemble the minimal context (phase-29) → one patch attempt (phase-30) → judge.
The loop stops on the **first** of:

- **green** — every test passes (`outcome=fixed`);
- **regression guard** — patches introduced regressions twice;
- **stall** — the failing-test set failed to shrink for ``repair_stall_threshold`` consecutive
  iterations (an oscillating set that stays the same size counts as not shrinking);
- **no patch** — ``repair_max_noop_attempts`` consecutive attempts wrote no file at all: the
  context is assembled deterministically, so asking again poses the identical question. The
  agent's own explanation of what it needed is what the human is shown (phase-63);
- **cap** — ``repair_max_iterations`` reached;
- **budget / cancellation / nothing-safe-to-patch** — halted for a reason worth telling a human.

Every non-green ending is an **escalation**, which is a first-class terminal state rather than an
error: the stage goes ``awaiting_user`` with a "here's where I'm stuck" payload (the failing tests,
the diffs already tried, the convergence metrics, and how to resume). Resuming with guidance
re-enters Build (phase-24), which may then re-run repair.

Thresholds are config-resolved (``admin > env > default``) so the admin dashboard can tune them
(phase-51/52) and the eval harness can sweep them (phase-44).
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from beanie import PydanticObjectId

from app.agents.repair import RepairAgent, RepairResult, test_key
from app.agents.repair_context import RepairContext, RepairContextAnalyzer
from app.agents.tools.context import ToolContext
from app.core.config import get_config
from app.core.errors import UserError
from app.db.models import Project, Run, TestRun
from app.db.models.common import utcnow
from app.db.models.enums import ArtifactType, RepairOutcome, Stage, StageStatus
from app.db.repos import RunRepo, StageStateRepo
from app.orchestrator.artifacts import ArtifactService
from app.realtime.hub import emit
from app.testing.models import TestResult, TestStatus

# Terminal loop outcomes (recorded on the Run — the eval unit, D13).
LOOP_FIXED = "fixed"
LOOP_ESCALATED = "escalated"

# Why the loop stopped short of green.
REASON_STALLED = "stalled"
REASON_REGRESSED = "regressed"
REASON_CAP = "cap_reached"
REASON_BUDGET = "budget"
REASON_BLOCKED = "blocked"
REASON_CANCELLED = "cancelled"
# The agent had files it could patch and wrote nothing — it could not fix the failure from the
# context it was given (phase-63). Distinct from `blocked`, where there was nothing editable at all.
REASON_NO_PATCH = "no_patch"
# The failures come from the sandbox, not the code — e.g. the test database has no mongod to run
# (phase-65). Escalated before any attempt: no patch can supply what the sandbox lacks, and asking
# is exactly how a model ends up re-deriving "no network" iteration after iteration.
REASON_ENVIRONMENT = "environment"

REPAIR_REPORT_KIND = "repair_report"

# The regression guard (default 2, config `repair_max_regressions`): one may be bad luck, twice is a
# pattern (§9). Promoted from a constant to config in phase-55 so the eval harness can sweep it.

# How the user resumes after an escalation — guidance re-enters Build (phase-24), which may
# then re-run repair. The controller states the contract; the conductor already implements it.
RESUME_ACTION = {
    "stage": str(Stage.build),
    "action": "refine",
    "hint": "Describe what to change; the build regenerates and repair can run again.",
}

_REASON_TEXT = {
    REASON_STALLED: "the failing tests stopped shrinking",
    REASON_REGRESSED: "my patches kept breaking tests that were passing",
    REASON_CAP: "I hit the iteration cap",
    REASON_BUDGET: "the budget cap halted the run",
    REASON_BLOCKED: "there was nothing safe for me to patch",
    REASON_NO_PATCH: "I could not fix it from the files I was given",
    REASON_CANCELLED: "the run was cancelled",
    REASON_ENVIRONMENT: "the sandbox environment is broken, not the code",
}

#: Guidance cannot fix an environment, so its escalation ends by saying what can.
_CLOSING = "Tell me what to change and I'll rebuild from your guidance."
_ENVIRONMENT_CLOSING = "Fix the environment, then run the tests again."


# --------------------------------------------------------------------- shapes


@dataclass
class ConvergenceMetrics:
    """What the eval harness (D13) measures: does the loop actually converge, and at what cost."""

    initial_failing: int = 0
    failing_by_iteration: list[int] = field(default_factory=list)
    regressions_introduced: int = 0
    #: Attempts that wrote no file at all — the loop spent a model call and changed nothing.
    noop_attempts: int = 0
    iterations: int = 0
    tokens_spent: int = 0
    cost_inr: float = 0.0
    wall_clock_s: float = 0.0

    @property
    def final_failing(self) -> int:
        return self.failing_by_iteration[-1] if self.failing_by_iteration else self.initial_failing

    def to_dict(self) -> dict[str, Any]:
        return {
            "initial_failing": self.initial_failing,
            "final_failing": self.final_failing,
            "failing_by_iteration": self.failing_by_iteration,
            "regressions_introduced": self.regressions_introduced,
            "noop_attempts": self.noop_attempts,
            "iterations": self.iterations,
            "tokens_spent": self.tokens_spent,
            "cost_inr": round(self.cost_inr, 6),
            "wall_clock_s": round(self.wall_clock_s, 3),
        }


@dataclass
class Escalation:
    """The "here's where I'm stuck" payload handed to a human."""

    reason: str
    summary: str
    failing_tests: list[dict[str, Any]]
    diffs_tried: list[dict[str, Any]]
    metrics: ConvergenceMetrics
    resume: dict[str, Any] = field(default_factory=lambda: dict(RESUME_ACTION))

    def to_dict(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "summary": self.summary,
            "failing_tests": self.failing_tests,
            "diffs_tried": self.diffs_tried,
            "metrics": self.metrics.to_dict(),
            "resume": self.resume,
        }


@dataclass
class RepairLoopResult:
    outcome: str  # LOOP_FIXED | LOOP_ESCALATED
    metrics: ConvergenceMetrics
    attempts: list[Any] = field(default_factory=list)  # RepairAttempt docs, in order
    final_run: TestRun | None = None
    escalation: Escalation | None = None

    @property
    def fixed(self) -> bool:
        return self.outcome == LOOP_FIXED

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "metrics": self.metrics.to_dict(),
            "attempts": [_attempt_public(a) for a in self.attempts],
            "final_run_id": str(self.final_run.id) if self.final_run else None,
            "escalation": self.escalation.to_dict() if self.escalation else None,
        }


class PatchAgent(Protocol):
    """The slice of :class:`~app.agents.repair.RepairAgent` the controller drives."""

    async def run(
        self,
        project: Project,
        run: Run,
        test_run: TestRun,
        *,
        context: RepairContext | None = None,
        ctx: ToolContext | None = None,
        channel: str | None = None,
        iteration: int = 1,
    ) -> RepairResult: ...


class ContextAnalyzer(Protocol):
    async def analyze(
        self, project: Project, test_run: TestRun, *, persist: bool = True
    ) -> RepairContext: ...


class RevertableWorkspace(Protocol):
    """The one git method the loop needs to undo an attempt that made things strictly worse."""

    async def restore(self, project: Project, sha: str) -> bool: ...


# --------------------------------------------------------------------- controller


class RepairLoopController:
    def __init__(
        self,
        agent: PatchAgent | None = None,
        analyzer: ContextAnalyzer | None = None,
        artifacts: ArtifactService | None = None,
        stages: StageStateRepo | None = None,
        runs: RunRepo | None = None,
        workspace: RevertableWorkspace | None = None,
        *,
        stage: Stage = Stage.test,
        owns_stage_status: bool = True,
    ) -> None:
        self._agent = agent or RepairAgent()
        self._analyzer = analyzer or RepairContextAnalyzer()
        self._artifacts = artifacts or ArtifactService()
        self._stages = stages or StageStateRepo()
        self._runs = runs or RunRepo()
        # Resolved lazily: a loop that never has to undo anything never opens a workspace.
        self._workspace = workspace
        # Which stage this loop belongs to (phase-55). Test owns its own status; Build does not —
        # the conductor owns build's status via `StageResult.next_status`, so the Build-reuse passes
        # `owns_stage_status=False`. Events + persisted attempts key on `self._stage` either way.
        self._stage = stage
        self._owns_stage_status = owns_stage_status

    async def run(
        self,
        project: Project,
        test_run: TestRun,
        *,
        run: Run | None = None,
        ctx: ToolContext | None = None,
        channel: str | None = None,
        cancel: asyncio.Event | None = None,
        max_iterations: int | None = None,
    ) -> RepairLoopResult:
        project_id = project.id
        if project_id is None:  # pragma: no cover - a persisted project always carries an id
            raise UserError("Project is not persisted")
        channel = channel or str(project_id)
        started = time.monotonic()

        config = get_config()
        if max_iterations is None:
            max_iterations = int(config.get("repair_max_iterations"))
        stall_threshold = int(config.get("repair_stall_threshold"))
        max_regressions = int(config.get("repair_max_regressions"))
        max_noop = int(config.get("repair_max_noop_attempts"))

        run = run or await self._runs.insert(Run(project_id=project_id, kind="repair:loop"))
        ctx = ctx or ToolContext.build(project, run, channel=channel)

        current = test_run
        failing = failing_keys(current)
        metrics = ConvergenceMetrics(initial_failing=len(failing))
        attempts: list[Any] = []
        escalation: Escalation | None = None
        stall = 0
        regressions = 0
        noop = 0

        if not failing:
            # Nothing to do — a green run in means a green run out (safe + idempotent).
            return await self._finish(
                project_id,
                run,
                channel,
                RepairLoopResult(LOOP_FIXED, metrics, [], current),
                started,
            )

        for iteration in range(1, max_iterations + 1):
            if cancel is not None and cancel.is_set():
                escalation = self._escalate(REASON_CANCELLED, "", current, attempts, metrics)
                break

            try:
                context = await self._analyzer.analyze(project, current)
                if context.environment is not None:
                    # Before the agent, so it costs no attempt and no MODEL_CODEGEN tokens.
                    finding = context.environment
                    escalation = self._escalate(
                        REASON_ENVIRONMENT,
                        f"{finding.reason[:1].upper()}{finding.reason[1:]}. {finding.hint}",
                        current,
                        attempts,
                        metrics,
                    )
                    break
                result = await self._agent.run(
                    project,
                    run,
                    current,
                    context=context,
                    ctx=ctx,
                    channel=channel,
                    iteration=iteration,
                )
            except UserError as exc:
                # Budget caps (and other user-facing halts) end the loop as an escalation, never
                # as a crash — "prefer escalation over silent failure".
                escalation = self._escalate(REASON_BUDGET, str(exc), current, attempts, metrics)
                break

            if result.attempt is None:
                # The agent had nothing safe to patch (e.g. only test files implicated).
                escalation = self._escalate(
                    REASON_BLOCKED, result.note or "", current, attempts, metrics
                )
                break

            after = result.test_run or current
            new_failing = failing_keys(after)
            shrank = len(new_failing) < len(failing)
            regressed = bool(result.newly_failing)
            # Strictly worse: it broke something that worked AND fixed nothing. An attempt that
            # trades one break for real progress is still progress (the guard counts it and stops
            # at `max_regressions`) — but pure damage is never worth keeping, so it is undone.
            # The loop may spend iterations; it may not hand back an app worse than it found.
            worsened = regressed and not shrank and not result.green

            metrics.iterations = iteration
            metrics.failing_by_iteration.append(len(new_failing))
            if regressed:
                regressions += 1
                metrics.regressions_introduced = regressions
            # An attempt that wrote nothing is not ordinary no-progress: the context is assembled
            # deterministically from the failing run, so the next iteration poses a byte-identical
            # question. One retry absorbs sampling variance; more only spends MODEL_CODEGEN tokens.
            if result.files_written:
                noop = 0
            else:
                noop += 1
                metrics.noop_attempts += 1

            result.attempt.outcome = _attempt_outcome(result.green, shrank, regressed)
            await result.attempt.save()
            attempts.append(result.attempt)

            await emit(
                channel,
                "repair.iteration",
                {
                    "i": iteration,
                    "failing_count": len(new_failing),
                    "regressions": regressions,
                    "tokens": run.cost.tokens,
                    "outcome": str(result.attempt.outcome),
                    "files": result.attempt.target_files,
                },
                stage=self._stage,
            )

            # Only advance onto the patched state when it was kept: after an undo the workspace
            # is back at the pre-attempt commit, so the pre-attempt run is what describes it.
            reverted = worsened and await self._revert(project, result, iteration, channel)
            if not reverted:
                current = after
                failing = new_failing
            if result.green:
                return await self._finish(
                    project_id,
                    run,
                    channel,
                    RepairLoopResult(LOOP_FIXED, metrics, attempts, current),
                    started,
                )

            # Regression guard is checked first: repeatedly breaking working code is a worse
            # signal than simply not converging, and it names the problem more precisely.
            if regressions >= max_regressions:
                escalation = self._escalate(REASON_REGRESSED, "", current, attempts, metrics)
                break

            # Before the stall guard: "I could not fix it from what you gave me" — with the
            # agent's own words — names the problem far more precisely than "it stopped shrinking".
            if noop >= max_noop:
                escalation = self._escalate(
                    REASON_NO_PATCH, result.summary, current, attempts, metrics
                )
                break

            stall = 0 if shrank else stall + 1
            if stall >= stall_threshold:
                escalation = self._escalate(REASON_STALLED, "", current, attempts, metrics)
                break
        else:
            # Ran the full budget of iterations without going green — the hard bound (§9).
            escalation = self._escalate(REASON_CAP, "", current, attempts, metrics)

        return await self._finish(
            project_id,
            run,
            channel,
            RepairLoopResult(LOOP_ESCALATED, metrics, attempts, current, escalation),
            started,
        )

    async def _revert(
        self, project: Project, result: RepairResult, iteration: int, channel: str
    ) -> bool:
        """Undo a strictly-worse attempt. Returns whether the workspace was actually restored.

        Fails soft on purpose: if the undo cannot be performed the loop carries on with the patch
        in place, exactly as it did before — a failed undo must never be worse than no undo.
        """
        if not result.before_sha:
            return False
        try:
            restored = await self._workspace_api().restore(project, result.before_sha)
        except Exception:  # noqa: BLE001 - an undo failure must not sink the loop
            restored = False
        if not restored:
            return False
        if result.attempt is not None:
            result.attempt.reverted = True
            await result.attempt.save()
        await emit(
            channel,
            "repair.reverted",
            {"i": iteration, "sha": result.before_sha, "files": result.files_written},
            stage=self._stage,
        )
        return True

    def _workspace_api(self) -> RevertableWorkspace:
        if self._workspace is None:
            from app.sandbox.workspace import WorkspaceService

            self._workspace = WorkspaceService()
        return self._workspace

    # -- terminal handling -----------------------------------------------------------------

    async def _finish(
        self,
        project_id: PydanticObjectId,
        run: Run,
        channel: str,
        result: RepairLoopResult,
        started: float,
    ) -> RepairLoopResult:
        result.metrics.wall_clock_s = time.monotonic() - started
        result.metrics.tokens_spent = run.cost.tokens
        result.metrics.cost_inr = run.cost.inr

        if result.escalation is not None:
            result.escalation.metrics = result.metrics
            # Escalation is a normal terminal state: hand the stage back to the human — but only
            # when this loop owns its stage status. In Build the conductor owns it (phase-55), so
            # setting it here would race the handler's own `StageResult.next_status`.
            if self._owns_stage_status:
                await self._stages.set_status(project_id, self._stage, StageStatus.awaiting_user)
            await emit(channel, "repair.escalation", result.escalation.to_dict(), stage=self._stage)
        else:
            await emit(
                channel,
                "repair.done",
                {"outcome": result.outcome, "metrics": result.metrics.to_dict()},
                stage=self._stage,
            )

        run.outcome = result.outcome
        run.finished_at = utcnow()
        await run.save()

        await self._persist(project_id, result)
        return result

    def _escalate(
        self,
        reason: str,
        detail: str,
        current: TestRun,
        attempts: list[Any],
        metrics: ConvergenceMetrics,
    ) -> Escalation:
        failing = failing_results(current)
        return Escalation(
            reason=reason,
            summary=_stuck_summary(reason, detail, failing, attempts, metrics),
            failing_tests=[
                {
                    "name": f.name,
                    "criterion_id": f.criterion_id,
                    "file": f.file,
                    "message": f.failure.message if f.failure else "",
                }
                for f in failing
            ],
            diffs_tried=[_attempt_public(a) for a in attempts],
            metrics=metrics,
        )

    async def _persist(self, project_id: PydanticObjectId, result: RepairLoopResult) -> None:
        """Store the loop report so the UI (phase-32) and eval (phase-44) can read it back."""
        await self._artifacts.create_version(
            project_id,
            self._stage,  # Build repairs persist under (build, repair_attempt) — a distinct trail.
            ArtifactType.repair_attempt,
            text=json.dumps(result.to_dict(), indent=2),
            meta={
                "kind": REPAIR_REPORT_KIND,
                "outcome": result.outcome,
                "iterations": result.metrics.iterations,
                "final_failing": result.metrics.final_failing,
                "regressions_introduced": result.metrics.regressions_introduced,
                "reason": result.escalation.reason if result.escalation else None,
            },
        )


# --------------------------------------------------------------------- pure helpers


def _attempt_public(attempt: Any) -> dict[str, Any]:
    """One attempt as the UI reads it — ``id`` is what the diff endpoint (phase-32) keys on."""
    return {
        "id": str(attempt.id) if attempt.id else None,
        "iteration": attempt.iteration,
        "target_files": attempt.target_files,
        "diff_ref": attempt.diff_ref,
        "outcome": str(attempt.outcome) if attempt.outcome else None,
        "reverted": bool(getattr(attempt, "reverted", False)),
    }


def _attempt_outcome(green: bool, shrank: bool, regressed: bool) -> RepairOutcome:
    """Judge one attempt.

    Regression dominates: an attempt that fixed something *and* broke something is still the
    dangerous kind. Otherwise progress (green or a smaller failing set) is ``fixed``, and standing
    still is ``no_progress``.
    """
    if regressed:
        return RepairOutcome.regressed
    if green or shrank:
        return RepairOutcome.fixed
    return RepairOutcome.no_progress


def failing_results(test_run: TestRun) -> list[TestResult]:
    """The run's failing results (``failures`` is pre-filtered; fall back to scanning results)."""
    raw = test_run.failures or test_run.results
    out: list[TestResult] = []
    for item in raw:
        try:
            result = TestResult.model_validate(item)
        except Exception:  # a malformed stored row must not sink the loop
            continue
        if result.status is TestStatus.failed:
            out.append(result)
    return out


def failing_keys(test_run: TestRun) -> set[str]:
    return {test_key(r) for r in failing_results(test_run)}


#: How much of the agent's own explanation the escalation carries — enough to name the file it
#: needed, not the whole transcript.
_MAX_DETAIL_CHARS = 600


def _clip(text: str, cap: int) -> str:
    text = text.strip()
    return text if len(text) <= cap else text[:cap].rstrip() + "…"


def _stuck_summary(
    reason: str,
    detail: str,
    failing: list[TestResult],
    attempts: list[Any],
    metrics: ConvergenceMetrics,
) -> str:
    why = _REASON_TEXT.get(reason, reason)
    names = ", ".join(f.name for f in failing[:5]) or "none"
    more = f" (+{len(failing) - 5} more)" if len(failing) > 5 else ""
    touched = sorted({p for a in attempts for p in a.target_files})
    tried = ", ".join(touched[:6]) if touched else "no files"
    trail = " → ".join(str(n) for n in metrics.failing_by_iteration) or "—"

    parts = [
        f"I stopped after {metrics.iterations} attempt(s) because {why}.",
        f"{len(failing)} test(s) are still failing: {names}{more}.",
        f"I patched: {tried}.",
        f"Failing count per attempt: {trail}.",
    ]
    if metrics.regressions_introduced:
        parts.append(f"{metrics.regressions_introduced} attempt(s) broke previously-passing tests.")
    if detail:
        parts.append(_clip(detail, _MAX_DETAIL_CHARS))
    parts.append(_ENVIRONMENT_CLOSING if reason == REASON_ENVIRONMENT else _CLOSING)
    return " ".join(parts)


__all__ = [
    "ConvergenceMetrics",
    "Escalation",
    "LOOP_ESCALATED",
    "LOOP_FIXED",
    "REASON_BLOCKED",
    "REASON_BUDGET",
    "REASON_CANCELLED",
    "REASON_CAP",
    "REASON_ENVIRONMENT",
    "REASON_NO_PATCH",
    "REASON_REGRESSED",
    "REASON_STALLED",
    "REPAIR_REPORT_KIND",
    "RESUME_ACTION",
    "RepairLoopController",
    "RepairLoopResult",
    "failing_keys",
    "failing_results",
]
