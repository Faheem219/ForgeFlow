"""Headless pipeline runner (phase-44, D13) — the measurement engine.

Runs a benchmark spec through the **real** pipeline with no UI attached:

    project → requirements (from the spec) → build → test → repair → [deploy] → [validate]

It drives the same conductor and the same handlers the UI drives, rather than reimplementing them.
That is the whole design: a harness that reimplements the pipeline measures the harness, not the
product, and drifts the moment a handler changes.

**The measurement that matters** is taken between build and repair. Tests are run once *before* the
repair loop (`first_pass`) and the loop's own final run supplies `post_repair`, so the contribution
— what the bounded repair loop actually adds — is a difference this runner observes rather than a
claim it repeats.

**Costly legs are opt-in.** Deploy and validate create billable provider resources, so they are off
unless asked for; CI runs the free legs. Each spec gets its own project, sandbox and database, and
they are torn down in a `finally` — a leaked sandbox or app DB per spec would make a full corpus run
expensive and eventually unrunnable.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from beanie import PydanticObjectId

from app.db.models import Project, Run, TestRun
from app.db.models.enums import Stage, StageStatus
from app.db.repos import StageStateRepo
from app.eval.metrics import (
    OUTCOME_DELIVERED,
    OUTCOME_ESCALATED,
    OUTCOME_FAILED,
    EvalReport,
    PassRate,
    SpecMetrics,
)
from app.eval.specs_loader import EvalSpec, load_all, spec_index
from app.orchestrator.requirements import RequirementsService
from app.orchestrator.schemas import Intent, IntentAction
from app.projects.service import ProjectService
from app.testing.models import TestStatus

logger = logging.getLogger(__name__)

#: Where `--out` writes by default; phase-45's dashboard reads these.
RESULTS_DIR = Path(__file__).resolve().parents[3] / "eval" / "results"


@dataclass(frozen=True)
class Legs:
    """Which legs of the pipeline to run. Costly ones are off by default (§7 cost discipline)."""

    build: bool = True
    test: bool = True
    repair: bool = True
    deploy: bool = False
    validate: bool = False

    @classmethod
    def with_deploy(cls) -> Legs:
        return cls(deploy=True, validate=True)

    def names(self) -> list[str]:
        return [n for n in ("build", "test", "repair", "deploy", "validate") if getattr(self, n)]


class _Conductor(Protocol):
    async def handle_intent(self, user_id: PydanticObjectId, intent: Intent) -> Any: ...


class _TestRunner(Protocol):
    """Only the call the runner makes — a full-suite run of the project as generated."""

    async def run(self, project: Project) -> TestRun: ...


class _RepairLoop(Protocol):
    async def run(self, project: Project, test_run: TestRun) -> Any: ...


#: Tears down one spec's sandbox + app database. Injected so tests never touch Docker.
Cleanup = Callable[[Project], Awaitable[None]]


def _pass_rate(run: TestRun | None) -> PassRate:
    if run is None:
        return PassRate()
    results = run.results or []
    passed = sum(1 for r in results if r.get("status") == TestStatus.passed)
    failed = sum(1 for r in results if r.get("status") == TestStatus.failed)
    return PassRate(total=len(results), passed=passed, failed=failed)


async def _default_cleanup(project: Project) -> None:  # pragma: no cover - needs Docker/a cluster
    """Reap the sandbox and drop the app database. Best-effort: cleanup must never fail a run."""
    from app.deploy.db_provision import DbProvisioner
    from app.sandbox.manager import get_manager

    try:
        await get_manager().destroy(project, remove_volume=True)
    except Exception:
        logger.warning("eval: could not destroy sandbox for %s", project.id, exc_info=True)
    try:
        await DbProvisioner().teardown(project, drop_data=True)
    except Exception:
        logger.warning("eval: could not drop app DB for %s", project.id, exc_info=True)


class EvalRunner:
    def __init__(
        self,
        *,
        conductor: _Conductor | None = None,
        tests: _TestRunner | None = None,
        repair: _RepairLoop | None = None,
        requirements: RequirementsService | None = None,
        projects: ProjectService | None = None,
        cleanup: Cleanup | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._conductor = conductor
        self._tests = tests
        self._repair = repair
        self._requirements = requirements or RequirementsService()
        self._projects = projects or ProjectService()
        self._cleanup = cleanup or _default_cleanup
        self._clock = clock
        self._stages = StageStateRepo()

    # -- lazily-resolved real collaborators (importing must not need Docker or a key) ---

    def _conductor_impl(self) -> _Conductor:
        if self._conductor is None:
            from app.orchestrator.conductor import Conductor

            self._conductor = Conductor()
        return self._conductor

    def _tests_impl(self) -> _TestRunner:
        if self._tests is None:
            from app.testing.runner import TestRunner

            self._tests = TestRunner()
        return self._tests

    def _repair_impl(self) -> _RepairLoop:
        if self._repair is None:
            from app.orchestrator.stages.repair import RepairLoopController

            self._repair = RepairLoopController()
        return self._repair

    # -- one spec ----------------------------------------------------------------------

    async def run_spec(
        self, spec: EvalSpec, *, legs: Legs | None = None, user_id: PydanticObjectId | None = None
    ) -> SpecMetrics:
        legs = legs or Legs()
        user_id = user_id or PydanticObjectId()
        metrics = SpecMetrics(spec_id=spec.id, title=spec.title, difficulty=str(spec.difficulty))
        started = self._clock()
        project: Project | None = None

        try:
            project = await self._projects.create_project(user_id, f"eval-{spec.id}")
            metrics.project_id = str(project.id)

            await self._requirements_leg(project, spec, metrics, started)
            if legs.build:
                await self._build_leg(project, spec, user_id, metrics, started)
            first_run = await self._test_leg(project, metrics, started) if legs.test else None
            if legs.repair:
                await self._repair_leg(project, first_run, metrics, started)
            if legs.deploy:
                await self._deploy_leg(project, user_id, metrics, started)
            if legs.validate:
                await self._validate_leg(project, user_id, metrics, started)

            metrics.outcome = _outcome(metrics, legs)
        except Exception as exc:  # a broken pipeline is a *result*, not a crashed harness
            metrics.outcome = OUTCOME_FAILED
            metrics.error = f"{type(exc).__name__}: {exc}"
            logger.warning("eval: spec %s failed", spec.id, exc_info=True)
        finally:
            metrics.wall_clock_seconds = round(self._clock() - started, 2)
            if project is not None:
                metrics.tokens, metrics.inr_cost = await _cost_of(project.id)
                await self._safe_cleanup(project)

        return metrics

    # -- legs --------------------------------------------------------------------------

    async def _requirements_leg(
        self, project: Project, spec: EvalSpec, metrics: SpecMetrics, started: float
    ) -> None:
        """The spec's requirements go in through the product's own service, verbatim."""
        assert project.id is not None
        await self._requirements.save(project.id, spec.requirements)
        await self._stages.set_status(project.id, Stage.requirements, StageStatus.complete)
        self._mark(metrics, "requirements", started)

    async def _build_leg(
        self,
        project: Project,
        spec: EvalSpec,
        user_id: PydanticObjectId,
        metrics: SpecMetrics,
        started: float,
    ) -> None:
        assert project.id is not None
        await self._conductor_impl().handle_intent(
            user_id,
            Intent(
                project_id=project.id,
                stage=Stage.build,
                action=IntentAction.proceed,
                message=spec.inputs.prompt or None,
            ),
        )
        self._mark(metrics, "build", started)

    async def _test_leg(
        self, project: Project, metrics: SpecMetrics, started: float
    ) -> TestRun | None:
        """The first-pass measurement: what the generated code scores before any repair."""
        run = await self._tests_impl().run(project)
        metrics.first_pass = _pass_rate(run)
        self._mark(metrics, "test", started)
        return run

    async def _repair_leg(
        self, project: Project, first_run: TestRun | None, metrics: SpecMetrics, started: float
    ) -> None:
        if first_run is None:
            return
        if metrics.first_pass.green:
            # Nothing to repair — post-repair equals first-pass rather than a missing number.
            metrics.post_repair = metrics.first_pass
            metrics.repair_outcome = "not_needed"
            return

        result = await self._repair_impl().run(project, first_run)
        metrics.repair_outcome = getattr(result, "outcome", None)
        loop_metrics = getattr(result, "metrics", None)
        metrics.repair_iterations = int(getattr(loop_metrics, "iterations", 0) or 0)
        metrics.regressions = int(getattr(loop_metrics, "regressions_introduced", 0) or 0)
        metrics.post_repair = _pass_rate(getattr(result, "final_run", None) or first_run)
        self._mark(metrics, "repair", started)

    async def _deploy_leg(
        self, project: Project, user_id: PydanticObjectId, metrics: SpecMetrics, started: float
    ) -> None:
        assert project.id is not None
        await self._conductor_impl().handle_intent(
            user_id,
            Intent(project_id=project.id, stage=Stage.deploy, action=IntentAction.proceed),
        )
        from app.db.models import Deployment

        deployment = (
            await Deployment.find({"project_id": project.id})
            .sort("-created_at", "-_id")
            .first_or_none()
        )
        if deployment is not None:
            metrics.live_url = deployment.urls.get("fe") or deployment.urls.get("be")
        if metrics.live_url:
            # The headline demo number: idea in, working URL out.
            metrics.screenshot_to_url_seconds = round(self._clock() - started, 2)
        self._mark(metrics, "deploy", started)

    async def _validate_leg(
        self, project: Project, user_id: PydanticObjectId, metrics: SpecMetrics, started: float
    ) -> None:
        assert project.id is not None
        await self._conductor_impl().handle_intent(
            user_id,
            Intent(project_id=project.id, stage=Stage.validate, action=IntentAction.proceed),
        )
        self._mark(metrics, "validate", started)

    # -- plumbing ----------------------------------------------------------------------

    def _mark(self, metrics: SpecMetrics, stage: str, started: float) -> None:
        elapsed = round(self._clock() - started, 2)
        metrics.stages_run.append(stage)
        metrics.stage_seconds[stage] = elapsed

    async def _safe_cleanup(self, project: Project) -> None:
        """Isolation is per-spec; cleanup failures are logged, never raised — a corpus run must
        not stop because one sandbox refused to die."""
        try:
            await self._cleanup(project)
        except Exception:
            logger.warning("eval: cleanup failed for %s", project.id, exc_info=True)

    # -- the corpus --------------------------------------------------------------------

    async def run_all(
        self, specs: list[EvalSpec] | None = None, *, legs: Legs | None = None
    ) -> EvalReport:
        legs = legs or Legs()
        report = EvalReport(legs=legs.names())
        for spec in specs if specs is not None else load_all():
            report.records.append(await self.run_spec(spec, legs=legs))
        from app.db.models.common import utcnow

        report.finished_at = utcnow()
        return report


def _outcome(metrics: SpecMetrics, legs: Legs) -> str:
    """Delivered means the thing actually works — green tests, and a live URL if deploy ran."""
    if legs.deploy and not metrics.live_url:
        return OUTCOME_ESCALATED
    if legs.test and not metrics.post_repair.green:
        return OUTCOME_ESCALATED
    return OUTCOME_DELIVERED


async def _cost_of(project_id: PydanticObjectId | None) -> tuple[int, float]:
    """Sum every costed unit of work this spec produced — one Run per agent activity (§6)."""
    if project_id is None:  # pragma: no cover - a created project always has an id
        return 0, 0.0
    runs = await Run.find({"project_id": project_id}).to_list()
    return sum(r.cost.tokens for r in runs), round(sum(r.cost.inr for r in runs), 4)


# --------------------------------------------------------------------- CLI


async def _run_cli(args: Any) -> int:
    from app.db.mongo import init_db

    await init_db()

    index = spec_index()
    if args.spec == "all":
        specs = list(load_all())
    else:
        matched = [s for sid, s in index.items() if sid == args.spec or sid.startswith(args.spec)]
        if not matched:
            print(f"No spec matches {args.spec!r}. Known: {', '.join(sorted(index))}")
            return 1
        specs = matched

    legs = Legs.with_deploy() if args.with_deploy else Legs()
    print(f"Running {len(specs)} spec(s) · legs: {', '.join(legs.names())}\n")

    report = await EvalRunner().run_all(specs, legs=legs)
    print(_format(report))

    out = args.out or (RESULTS_DIR / f"eval-{int(time.time())}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report.to_json(), encoding="utf-8")
    print(f"\nWrote {out}")
    return 0


def _format(report: EvalReport) -> str:
    rows = [("SPEC", "OUTCOME", "FIRST", "POST", "ITER", "TOKENS", "SECONDS")]
    for r in report.records:
        rows.append(
            (
                r.spec_id,
                r.outcome,
                _pct(r.first_pass.rate),
                _pct(r.post_repair.rate),
                str(r.repair_iterations),
                str(r.tokens),
                f"{r.wall_clock_seconds:.1f}",
            )
        )
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    table = "\n".join(
        "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)) for row in rows
    )

    agg = report.aggregate()
    summary = (
        f"\n{agg['specs']} specs · {agg['delivered']} delivered · {agg['escalated']} escalated · "
        f"{agg['failed']} failed\n"
        f"first-pass {_pct(agg['mean_first_pass_rate'])} → post-repair "
        f"{_pct(agg['mean_post_repair_rate'])} "
        f"(delta {_pct(agg['mean_repair_delta'])}, {agg['specs_improved_by_repair']} improved)\n"
        f"{agg['total_repair_iterations']} repair iterations · {agg['total_tokens']} tokens · "
        f"₹{agg['total_inr']}"
    )
    return table + "\n" + summary


def _pct(rate: float | None) -> str:
    return "—" if rate is None else f"{rate * 100:.0f}%"


def main(argv: list[str] | None = None) -> int:
    """``uv run python -m app.eval.runner --spec todo-list [--with-deploy]``"""
    import argparse
    import asyncio

    parser = argparse.ArgumentParser(description="Run the benchmark specs headlessly.")
    parser.add_argument("--spec", default="all", help="spec id (or a prefix), or 'all'")
    parser.add_argument(
        "--with-deploy",
        action="store_true",
        help="also run deploy + validate (creates billable provider resources)",
    )
    parser.add_argument("--out", type=Path, default=None, help="where to write the report JSON")
    args = parser.parse_args(argv)

    return asyncio.run(_run_cli(args))


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())


__all__ = ["EvalRunner", "Legs", "RESULTS_DIR"]
