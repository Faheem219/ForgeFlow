"""Live-failure repair path (phase-40) — the loop that makes a deployed URL *verified*.

A live failure is not a dead end (D12): it re-enters Build. This controller closes the loop:

    validate → diagnose → repair (phase-31) → redeploy (phase-37) → re-validate

and it is **bounded at both levels**. The inner repair loop already caps its iterations (phase-31);
this adds the *outer* cap (``validate_max_cycles``), because a broken deploy configuration would
otherwise let a converging repair loop drive redeploys forever — the same "never unbounded"
invariant applied one level up (D7).

Diagnosis matters as much as the loop. Patching application code cannot fix a missing environment
variable or a backend that never came up, so those failures are classified as **env** and escalated
with the evidence rather than burned through repair cycles. Only failures that look like real
product bugs reach the repair agent.

Every collaborator (live runner, repair loop, deploy orchestrator) is injected, so the whole cycle
is exercised without Docker, a model, or a provider.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from beanie import PydanticObjectId

from app.core.config import get_config
from app.core.errors import ProviderError, UserError
from app.db.blobs import get_blob_store
from app.db.models import Deployment, Project, Run, TestRun
from app.db.models.common import utcnow
from app.db.models.enums import (
    ArtifactType,
    DeployMode,
    MessageRole,
    Stage,
    StageStatus,
    TestEnv,
)
from app.db.repos import RunRepo, StageStateRepo
from app.deploy.orchestrate import STATUS_LIVE, DeployOrchestrator
from app.orchestrator.artifacts import ArtifactService
from app.orchestrator.schemas import Intent, IntentAction, OutboundMessage, StageResult
from app.realtime.hub import emit
from app.realtime.schemas import EventType
from app.testing.live import LiveTestRunner
from app.testing.models import TestStatus

if TYPE_CHECKING:  # avoid an import cycle: stages/__init__ imports this module
    from app.orchestrator.stages.base import StageContext

VALIDATION_REPORT_KIND = "validation_report"

# Terminal outcomes.
OUTCOME_VALIDATED = "validated"
OUTCOME_ESCALATED = "escalated"

# Why validation stopped short of a verified deployment.
REASON_CAP = "cycle_cap"
REASON_ENV = "env_config"
REASON_REPAIR = "repair_escalated"
REASON_DEPLOY = "redeploy_failed"

# How a live failure was classified.
DIAGNOSIS_CODE = "code"  # a real product bug → the repair loop can act on it
DIAGNOSIS_ENV = "env"  # config/infra → a code patch cannot fix this

#: Signatures of "the site isn't wired up right", as opposed to "the feature is wrong". Matched
#: against failure text; deliberately conservative, since misreading a real bug as an env problem
#: would skip a repair the loop could have made.
_ENV_SIGNALS = (
    "err_connection",
    "err_name_not_resolved",
    "econnrefused",
    "enotfound",
    "net::",
    "502 bad gateway",
    "503 service unavailable",
    "504 gateway",
    "cors",
    "failed to fetch",
    "network error",
    "timeout exceeded while waiting for the page",
)
_ENV_STATUS = re.compile(r"\b(50[0-9])\b")


class _LiveRunner(Protocol):
    async def run(self, project: Project, *, base_url: str | None = None) -> TestRun: ...


class _RepairLoop(Protocol):
    """The phase-31 loop, seeded with the *live* failures rather than a sandbox run."""

    async def run(self, project: Project, test_run: TestRun) -> Any: ...


class _Deployer(Protocol):
    async def deploy(
        self, project: Project, *, mode: DeployMode, user_id: PydanticObjectId | None = None
    ) -> Deployment: ...


@dataclass
class ValidationCycle:
    """One pass of the outer loop, recorded for the timeline the UI draws."""

    index: int
    live_run_id: str | None = None
    failing: int = 0
    green: bool = False
    diagnosis: str | None = None
    repair_outcome: str | None = None
    redeploy_status: str | None = None
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "live_run_id": self.live_run_id,
            "failing": self.failing,
            "green": self.green,
            "diagnosis": self.diagnosis,
            "repair_outcome": self.repair_outcome,
            "redeploy_status": self.redeploy_status,
            "note": self.note,
        }


@dataclass
class ValidationReport:
    outcome: str
    url: str | None = None
    cycles: list[ValidationCycle] = field(default_factory=list)
    live_run_id: str | None = None
    reason: str | None = None
    summary: str = ""
    failing_tests: list[dict[str, Any]] = field(default_factory=list)
    repair_escalation: dict[str, Any] | None = None
    wall_clock_s: float = 0.0

    @property
    def validated(self) -> bool:
        return self.outcome == OUTCOME_VALIDATED

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "url": self.url,
            "cycles": [c.to_dict() for c in self.cycles],
            "live_run_id": self.live_run_id,
            "reason": self.reason,
            "summary": self.summary,
            "failing_tests": self.failing_tests,
            "repair_escalation": self.repair_escalation,
            "wall_clock_s": round(self.wall_clock_s, 2),
        }


# --------------------------------------------------------------------- diagnosis (pure)


def diagnose(
    failures: list[dict[str, Any]], deploy_status: str, deploy_log: str
) -> tuple[str, str]:
    """Classify a live failure as a code bug or an environment problem, with the reason.

    The distinction is what stops the loop wasting repair iterations: a patch cannot fix a service
    that never came up or a frontend pointed at the wrong API. Returns ``(diagnosis, explanation)``.
    """
    if deploy_status and deploy_status != STATUS_LIVE:
        return (
            DIAGNOSIS_ENV,
            f"the deployment itself is {deploy_status}, so the site is not fully up",
        )

    haystack = " ".join(
        str(part).lower()
        for failure in failures
        for part in (
            failure.get("name", ""),
            (failure.get("failure") or {}).get("message", ""),
            (failure.get("failure") or {}).get("stack", ""),
        )
    )
    for signal in _ENV_SIGNALS:
        if signal in haystack:
            return DIAGNOSIS_ENV, f"live failures look like connectivity, not logic ({signal!r})"
    if _ENV_STATUS.search(haystack):
        return DIAGNOSIS_ENV, "the deployed backend answered with a 5xx"
    if "error" in deploy_log.lower() and not failures:
        return DIAGNOSIS_ENV, "the suite could not run and the deploy log shows errors"

    return DIAGNOSIS_CODE, "failures name application behaviour, so the repair loop can act"


def _failing(run: TestRun) -> list[dict[str, Any]]:
    return [f for f in run.failures if f.get("status") == TestStatus.failed] or list(run.failures)


def _is_green(run: TestRun) -> bool:
    results = run.results or []
    passed = sum(1 for r in results if r.get("status") == TestStatus.passed)
    failed = sum(1 for r in results if r.get("status") == TestStatus.failed)
    return failed == 0 and passed > 0


# --------------------------------------------------------------------- the outer loop


class LiveValidationController:
    def __init__(
        self,
        live_runner: _LiveRunner | None = None,
        repair: _RepairLoop | None = None,
        deployer: _Deployer | None = None,
        artifacts: ArtifactService | None = None,
        stages: StageStateRepo | None = None,
        runs: RunRepo | None = None,
        emitter: Any = emit,
    ) -> None:
        self._live = live_runner
        self._repair = repair
        self._deployer = deployer
        self._artifacts = artifacts or ArtifactService()
        self._stages = stages or StageStateRepo()
        self._runs = runs or RunRepo()
        self._emit = emitter

    def _live_runner(self) -> _LiveRunner:
        if self._live is None:
            self._live = LiveTestRunner()
        return self._live

    def _repair_loop(self) -> _RepairLoop:
        if self._repair is None:
            from app.orchestrator.stages.repair import RepairLoopController

            self._repair = RepairLoopController()
        return self._repair

    def _deploy_orchestrator(self) -> _Deployer:
        if self._deployer is None:
            self._deployer = DeployOrchestrator()
        return self._deployer

    async def run(
        self, project: Project, *, mode: DeployMode = DeployMode.seamless, run: Run | None = None
    ) -> ValidationReport:
        project_id = project.id
        if project_id is None:  # pragma: no cover - a persisted project always carries an id
            raise UserError("Project is not persisted")
        channel = str(project_id)
        started = time.monotonic()

        max_cycles = max(1, int(get_config().get("validate_max_cycles")))
        report = ValidationReport(outcome=OUTCOME_ESCALATED)

        for index in range(1, max_cycles + 1):
            cycle = ValidationCycle(index=index)
            report.cycles.append(cycle)

            # 1) Validate against the live site.
            await self._step(channel, "validating", cycle=index)
            live_run = await self._live_runner().run(project)
            cycle.live_run_id = str(live_run.id)
            report.live_run_id = str(live_run.id)
            failures = _failing(live_run)
            cycle.failing = len(failures)
            cycle.green = _is_green(live_run)

            if cycle.green:
                report.outcome = OUTCOME_VALIDATED
                report.url = await self._live_url(project_id)
                report.summary = f"Live validation passed on cycle {index}."
                await self._step(channel, "validated", cycle=index, url=report.url)
                return await self._finish(project_id, report, run, started, channel)

            report.failing_tests = failures

            # 2) Diagnose: a code bug the repair loop can fix, or an env problem it cannot.
            deployment = await self._latest_deployment(project_id)
            diagnosis, why = diagnose(
                failures,
                deployment.status if deployment else "",
                await self._deploy_log(deployment),
            )
            cycle.diagnosis = diagnosis
            cycle.note = why
            await self._step(
                channel,
                "diagnosed",
                cycle=index,
                diagnosis=diagnosis,
                reason=why,
                failing=len(failures),
            )

            if diagnosis == DIAGNOSIS_ENV:
                report.reason = REASON_ENV
                report.summary = (
                    f"Live validation failed for a configuration reason — {why}. "
                    "A code patch would not fix this: check the deployment's env wiring "
                    "(backend URL, database URI, provider keys) and deploy again."
                )
                return await self._finish(project_id, report, run, started, channel)

            # 3) Repair in Build — the inner loop, already bounded (phase-31).
            await self._step(channel, "repairing", cycle=index)
            repair_result = await self._repair_loop().run(project, live_run)
            cycle.repair_outcome = getattr(repair_result, "outcome", None)
            if not getattr(repair_result, "fixed", False):
                escalation = getattr(repair_result, "escalation", None)
                report.reason = REASON_REPAIR
                report.repair_escalation = escalation.to_dict() if escalation else None
                report.summary = (
                    "The live failures could not be repaired automatically — the bounded repair "
                    "loop escalated. Review the attempts and guide the build."
                )
                return await self._finish(project_id, report, run, started, channel)

            # 4) Redeploy the fix, then loop round to re-validate.
            await self._step(channel, "redeploying", cycle=index)
            try:
                deployment = await self._deploy_orchestrator().deploy(
                    project, mode=mode, user_id=project.user_id
                )
            except ProviderError as exc:
                cycle.redeploy_status = "failed"
                report.reason = REASON_DEPLOY
                report.summary = f"The repair was made but the redeploy failed: {exc}"
                return await self._finish(project_id, report, run, started, channel)

            cycle.redeploy_status = deployment.status
            if deployment.status != STATUS_LIVE:
                report.reason = REASON_DEPLOY
                report.summary = (
                    f"The repair was made but the redeploy came back {deployment.status}. "
                    "Check the topology for the failing component."
                )
                return await self._finish(project_id, report, run, started, channel)

        # 5) Cap reached — the outer bound that keeps a bad deploy config from looping forever.
        report.reason = REASON_CAP
        report.summary = (
            f"Stopped after {max_cycles} repair→redeploy→re-validate "
            f"{'cycle' if max_cycles == 1 else 'cycles'} without a green live run. "
            "The remaining failures need a human decision."
        )
        return await self._finish(project_id, report, run, started, channel)

    # -- inputs ------------------------------------------------------------------------

    async def _latest_deployment(self, project_id: PydanticObjectId) -> Deployment | None:
        return (
            await Deployment.find({"project_id": project_id})
            .sort("-created_at", "-_id")
            .first_or_none()
        )

    async def _live_url(self, project_id: PydanticObjectId) -> str | None:
        deployment = await self._latest_deployment(project_id)
        if deployment is None:
            return None
        return deployment.urls.get("fe") or deployment.urls.get("be")

    async def _deploy_log(self, deployment: Deployment | None) -> str:
        """Deploy logs join the diagnosis — an env problem often shows there, not in the tests."""
        if deployment is None or not deployment.logs_ref:
            return ""
        try:
            return (await get_blob_store().get(deployment.logs_ref)).decode("utf-8")
        except Exception:  # a missing blob just means less evidence, not a failure
            return ""

    # -- outputs -----------------------------------------------------------------------

    async def _finish(
        self,
        project_id: PydanticObjectId,
        report: ValidationReport,
        run: Run | None,
        started: float,
        channel: str,
    ) -> ValidationReport:
        report.wall_clock_s = time.monotonic() - started
        if not report.validated:
            await self._step(channel, "escalated", reason=report.reason, summary=report.summary)
        if run is not None:
            run.outcome = report.outcome
            run.finished_at = utcnow()
            await run.save()
        await self._persist(project_id, report)
        return report

    async def _persist(self, project_id: PydanticObjectId, report: ValidationReport) -> None:
        """Store the report so the UI hydrates after a reload and eval (phase-44) can read it."""
        await self._artifacts.create_version(
            project_id,
            Stage.validate,
            ArtifactType.test_result,
            text=json.dumps(report.to_dict(), indent=2),
            meta={
                "kind": VALIDATION_REPORT_KIND,
                "outcome": report.outcome,
                "cycles": len(report.cycles),
                "reason": report.reason,
                "url": report.url,
                "env": str(TestEnv.live),
            },
        )

    async def _step(self, channel: str, step: str, **payload: Any) -> None:
        await self._emit(
            channel,
            EventType.validate_status,
            {"step": step, **payload},
            stage=Stage.validate,
        )


# --------------------------------------------------------------------- stage handler


class ValidateStageHandler:
    """The Validate stage: prove the deployed URL works, and fix it when it doesn't.

    ``proceed``/``refine`` run the bounded live-validation cycle; a verified deployment leaves the
    stage ``complete`` with the final live link, anything else ``awaiting_user`` with the reason.
    ``skip`` opts out. The conductor enforces validate⇐deploy *before* this runs.
    """

    def __init__(self, controller: LiveValidationController | None = None) -> None:
        self._controller = controller
        self._runs = RunRepo()

    async def handle(self, intent: Intent, ctx: StageContext) -> StageResult:
        if intent.action is IntentAction.skip:
            return StageResult(
                messages=[
                    OutboundMessage(
                        role=MessageRole.assistant,
                        content=("Validation skipped — the deployment is live but unverified."),
                    )
                ]
            )

        project = ctx.project
        project_id = project.id
        if project_id is None:  # pragma: no cover - a persisted project always carries an id
            raise UserError("Project is not persisted")

        mode = _resolve_mode(intent, ctx.config)
        controller = self._controller or LiveValidationController()

        run = await self._runs.insert(Run(project_id=project_id, kind="validate"))
        report = await controller.run(project, mode=mode, run=run)

        next_status = StageStatus.complete if report.validated else StageStatus.awaiting_user
        return StageResult(
            messages=[OutboundMessage(role=MessageRole.assistant, content=_summarize(report))],
            next_status=next_status,
            events=[
                {
                    "event": "progress",
                    "payload": {
                        "stage": "validate",
                        "status": str(next_status),
                        "outcome": report.outcome,
                        "url": report.url,
                        "cycles": len(report.cycles),
                        "run_id": str(run.id),
                    },
                }
            ],
        )


def _resolve_mode(intent: Intent, config: Any) -> DeployMode:
    raw = str(intent.payload.get("mode") or config.get("deploy_default_mode")).strip().lower()
    return DeployMode.byo if raw == DeployMode.byo.value else DeployMode.seamless


def _summarize(report: ValidationReport) -> str:
    if report.validated:
        lines = ["Live validation passed — the deployment is verified."]
        if report.url:
            lines.append(f"Your app is live at: {report.url}")
        if len(report.cycles) > 1:
            lines.append(f"It took {len(report.cycles)} repair→redeploy cycles to get there.")
        return "\n".join(lines)

    lines = [report.summary or "Live validation did not pass."]
    if report.failing_tests:
        names = [str(f.get("name", "?")) for f in report.failing_tests[:5]]
        lines.append("Failing live tests: " + ", ".join(names))
    return "\n".join(lines)


__all__ = [
    "DIAGNOSIS_CODE",
    "DIAGNOSIS_ENV",
    "LiveValidationController",
    "OUTCOME_ESCALATED",
    "OUTCOME_VALIDATED",
    "REASON_CAP",
    "REASON_ENV",
    "REASON_REPAIR",
    "VALIDATION_REPORT_KIND",
    "ValidateStageHandler",
    "ValidationReport",
    "diagnose",
]
