"""Shared fakes for the live-validation cycle (phase-40).

The controller composes three heavy subsystems — the live runner, the repair loop and the deploy
orchestrator — so all three are injected. That lets the whole validate→repair→redeploy→re-validate
cycle run with no Docker, no model, and no provider.
"""

from __future__ import annotations

from typing import Any

from beanie import PydanticObjectId

from app.db.models import Deployment, Project, TestRun
from app.db.models.enums import DeployMode, Stage, StageStatus, TestEnv
from app.db.repos import StageStateRepo
from app.deploy.orchestrate import STATUS_LIVE
from app.projects.service import ProjectService

FE_URL = "https://forgeflow-web.vercel.app"


def result(name: str, status: str, criterion: str, message: str = "") -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "framework": "playwright",
        "criterion_id": criterion,
        "file": "e2e/todos.spec.ts",
        "failure": {"message": message, "files_referenced": []} if message else None,
    }


PASSING = [result("[ac-list] shows todos", "passed", "ac-list")]
CODE_FAILURE = [
    result("[ac-list] shows todos", "passed", "ac-list"),
    result(
        "[ac-add] adds a todo",
        "failed",
        "ac-add",
        "expect(locator).toBeVisible() failed: the new todo never rendered",
    ),
]
ENV_FAILURE = [
    result(
        "[ac-list] shows todos",
        "failed",
        "ac-list",
        "page.goto: net::ERR_CONNECTION_REFUSED at https://forgeflow-web.vercel.app",
    )
]


async def project_ready_to_validate(name: str = "app") -> Project:
    """A project with a completed build + deploy — validate's hard prereq chain."""
    project = await ProjectService().create_project(PydanticObjectId(), name)
    assert project.id is not None
    stages = StageStateRepo()
    await stages.set_status(project.id, Stage.build, StageStatus.complete)
    await stages.set_status(project.id, Stage.deploy, StageStatus.complete)
    await Deployment(
        project_id=project.id,
        mode=DeployMode.seamless,
        urls={"fe": FE_URL, "be": "https://api.onrender.com"},
        status=STATUS_LIVE,
    ).insert()
    return project


class FakeLiveRunner:
    """Serves a scripted sequence of live runs — one per outer cycle."""

    def __init__(self, *runs: list[dict[str, Any]]) -> None:
        self._scripted = list(runs)
        self.calls = 0

    async def run(self, project: Project, *, base_url: str | None = None) -> TestRun:
        results = self._scripted[min(self.calls, len(self._scripted) - 1)]
        self.calls += 1
        return await TestRun(
            project_id=project.id,
            results=results,
            failures=[r for r in results if r["status"] == "failed"],
            env=TestEnv.live,
        ).insert()


class FakeRepairResult:
    def __init__(self, outcome: str, escalation: Any = None) -> None:
        self.outcome = outcome
        self.escalation = escalation

    @property
    def fixed(self) -> bool:
        return self.outcome == "fixed"


class FakeEscalation:
    def __init__(self, reason: str = "stalled") -> None:
        self.reason = reason

    def to_dict(self) -> dict[str, Any]:
        return {"reason": self.reason, "summary": "no progress across iterations"}


class FakeRepairLoop:
    def __init__(self, *outcomes: FakeRepairResult) -> None:
        self._scripted = list(outcomes) or [FakeRepairResult("fixed")]
        self.calls: list[str] = []

    async def run(self, project: Project, test_run: TestRun, **kwargs: Any) -> FakeRepairResult:
        self.calls.append(str(test_run.id))
        return self._scripted[min(len(self.calls) - 1, len(self._scripted) - 1)]


class FakeDeployer:
    def __init__(self, status: str = STATUS_LIVE, error: Exception | None = None) -> None:
        self.status = status
        self.error = error
        self.calls = 0

    async def deploy(
        self, project: Project, *, mode: DeployMode, user_id: PydanticObjectId | None = None
    ) -> Deployment:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return await Deployment(
            project_id=project.id,
            mode=mode,
            urls={"fe": FE_URL},
            status=self.status,
        ).insert()


class RecordingEmitter:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def __call__(
        self, channel: str, event_type: object, payload: dict[str, Any], stage: object = None
    ) -> None:
        self.events.append((str(event_type), payload))

    def steps(self) -> list[str]:
        return [str(p.get("step")) for _e, p in self.events if "step" in p]
