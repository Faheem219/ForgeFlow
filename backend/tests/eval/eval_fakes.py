"""Fakes for the headless eval runner (phase-44).

The runner composes the heaviest subsystems in the project — the conductor (and through it codegen),
the test runner, the repair loop — so all three are injected. That lets the measurement logic, which
is what this phase actually contributes, be tested without a model, a sandbox or a provider.
"""

from __future__ import annotations

from typing import Any

from beanie import PydanticObjectId

from app.db.models import Deployment, Project, Run, TestRun
from app.db.models.enums import DeployMode, Stage, TestEnv
from app.eval.specs_loader import EvalSpec

SPEC_YAML: dict[str, Any] = {
    "id": "sample",
    "title": "Sample app",
    "difficulty": "simple",
    "inputs": {"prompt": "build a sample"},
    "requirements": {
        "features": [
            {
                "name": "Do the thing",
                "description": "It does the thing.",
                "acceptance_criteria": [
                    {"text": "The thing is done.", "kind": "unit"},
                    {"text": "The thing is shown.", "kind": "e2e"},
                ],
            }
        ]
    },
    "expected_features": ["Do the thing"],
}


def make_spec(**overrides: Any) -> EvalSpec:
    return EvalSpec.model_validate({**SPEC_YAML, **overrides})


def results(passed: int, failed: int) -> list[dict[str, Any]]:
    return [
        {"name": f"pass {i}", "status": "passed", "framework": "jest"} for i in range(passed)
    ] + [{"name": f"fail {i}", "status": "failed", "framework": "jest"} for i in range(failed)]


class FakeConductor:
    """Records every intent the runner submits, and can fail a chosen stage."""

    def __init__(self, *, fail_on: Stage | None = None) -> None:
        self.intents: list[tuple[str, str]] = []
        self._fail_on = fail_on

    async def handle_intent(self, user_id: PydanticObjectId, intent: Any) -> Any:
        self.intents.append((str(intent.stage), str(intent.action)))
        if self._fail_on is not None and intent.stage is self._fail_on:
            raise RuntimeError(f"{intent.stage} blew up")
        return None

    def stages(self) -> list[str]:
        return [stage for stage, _action in self.intents]


class FakeTests:
    """Serves a scripted first-pass result set, and records which projects it ran against."""

    def __init__(self, passed: int = 1, failed: int = 1) -> None:
        self._passed = passed
        self._failed = failed
        self.projects: list[str] = []

    async def run(self, project: Project) -> TestRun:
        self.projects.append(str(project.id))
        rows = results(self._passed, self._failed)
        return await TestRun(
            project_id=project.id,
            results=rows,
            failures=[r for r in rows if r["status"] == "failed"],
            env=TestEnv.sandbox,
        ).insert()


class FakeLoopMetrics:
    def __init__(self, iterations: int, regressions: int = 0) -> None:
        self.iterations = iterations
        self.regressions_introduced = regressions


class FakeRepairResult:
    def __init__(self, outcome: str, final_run: TestRun | None, metrics: FakeLoopMetrics) -> None:
        self.outcome = outcome
        self.final_run = final_run
        self.metrics = metrics

    @property
    def fixed(self) -> bool:
        return self.outcome == "fixed"


class FakeRepair:
    """Repairs to a scripted post-repair result set."""

    def __init__(
        self,
        *,
        passed: int = 2,
        failed: int = 0,
        iterations: int = 2,
        regressions: int = 0,
        outcome: str = "fixed",
    ) -> None:
        self._passed = passed
        self._failed = failed
        self._iterations = iterations
        self._regressions = regressions
        self._outcome = outcome
        self.calls: list[str] = []

    async def run(self, project: Project, test_run: TestRun) -> FakeRepairResult:
        self.calls.append(str(test_run.id))
        rows = results(self._passed, self._failed)
        final = await TestRun(
            project_id=project.id,
            results=rows,
            failures=[r for r in rows if r["status"] == "failed"],
            env=TestEnv.sandbox,
        ).insert()
        return FakeRepairResult(
            self._outcome, final, FakeLoopMetrics(self._iterations, self._regressions)
        )


class RecordingCleanup:
    """Stands in for reaping the sandbox + app DB; records what it was asked to clean."""

    def __init__(self, *, explode: bool = False) -> None:
        self.cleaned: list[str] = []
        self._explode = explode

    async def __call__(self, project: Project) -> None:
        self.cleaned.append(str(project.id))
        if self._explode:
            raise RuntimeError("sandbox refused to die")


class StepClock:
    """Deterministic wall clock: every read advances by a fixed step."""

    def __init__(self, step: float = 1.0) -> None:
        self.step = step
        self.now = 0.0

    def __call__(self) -> float:
        value = self.now
        self.now += self.step
        return value


async def seed_cost(project_id: PydanticObjectId, tokens: int, inr: float) -> Run:
    """A costed Run, as the real agents produce — the runner sums these per spec."""
    run = Run(project_id=project_id, kind="codegen:build")
    run.cost.tokens = tokens
    run.cost.inr = inr
    return await run.insert()


async def seed_deployment(project_id: PydanticObjectId, url: str = "https://web.app") -> Deployment:
    return await Deployment(
        project_id=project_id, mode=DeployMode.seamless, urls={"fe": url}, status="live"
    ).insert()
