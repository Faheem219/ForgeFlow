"""A build must outlive the request (and the browser tab) that started it.

The failure this pins down: `POST /intent` used to run the whole build inline, so a page refresh
abandoned the response and the UI lost every trace of work that was in fact still running. Builds
are now detached — the request is acknowledged at once and the run is discoverable afterwards from
its `Run` document, which is also what makes the progress survive the bounded realtime replay ring.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest
from beanie import PydanticObjectId

import app.orchestrator.stages as stages_mod
from app.agents.codegen import BuildReport
from app.core.errors import UserError
from app.db.models import Project, Run
from app.db.models.enums import Stage, StageStatus
from app.db.repos import RunRepo
from app.orchestrator.conductor import (
    BUILD_RUN_KINDS,
    Conductor,
    drain_detached,
    reap_orphaned_runs,
)
from app.orchestrator.schemas import Intent, IntentAction
from app.orchestrator.stages import register_handler
from app.orchestrator.stages.build import BuildStageHandler
from app.orchestrator.stages.build_verify import BuildVerifyReport
from app.projects.service import ProjectService

pytestmark = pytest.mark.usefixtures("mongo_db")


class _OkVerifier:
    """A no-op verifier: these tests exercise the run lifecycle, not verification (phase-55)."""

    async def run(self, project: object, run: object, **kwargs: object) -> BuildVerifyReport:
        return BuildVerifyReport(
            ok=True, message="Verified.", fe_status="running", be_status="running"
        )


def _handler(codegen: object) -> BuildStageHandler:
    return BuildStageHandler(codegen, _OkVerifier())  # type: ignore[arg-type]


def _report(boot: str = "healthy") -> BuildReport:
    return BuildReport(
        boot_status=boot,
        files_changed=["backend/src/features/todo/todo.routes.ts"],
        features_built=["Todos"],
        follow_ups=[],
        notes=[],
        commit="abcdef1234",
        tests_passed=True,
        summary="Built the Todos feature.",
        plan="Plan.",
    )


class SlowCodegen:
    """Blocks until released, so the test can inspect the world mid-build."""

    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.started = asyncio.Event()
        self.runs: list[Run] = []

    async def run(self, project: Project, run: Run, **kwargs: object) -> BuildReport:
        self.runs.append(run)
        run.progress.step = "implement"
        run.progress.label = "Installing dependencies"
        run.progress.files = ["backend/src/features/todo/todo.model.ts"]
        await run.save()
        self.started.set()
        await self.release.wait()
        return _report()

    async def finalize_report(
        self, project_id: object, report: BuildReport, verification: object, **kwargs: object
    ) -> BuildReport:
        return report


@pytest.fixture
def restore_registry() -> Iterator[None]:
    snapshot = dict(stages_mod._HANDLERS)
    yield
    stages_mod._HANDLERS.clear()
    stages_mod._HANDLERS.update(snapshot)


async def _project() -> tuple[PydanticObjectId, PydanticObjectId]:
    user = PydanticObjectId()
    project = await ProjectService().create_project(user, "p")
    assert project.id is not None
    return user, project.id


async def test_intent_returns_before_the_build_finishes(restore_registry: None) -> None:
    user, pid = await _project()
    codegen = SlowCodegen()
    register_handler(Stage.build, _handler(codegen))

    resp = await Conductor().handle_intent(
        user, Intent(project_id=pid, stage=Stage.build, action=IntentAction.proceed)
    )

    # Answered while the agent is still working — that is the whole point.
    await asyncio.wait_for(codegen.started.wait(), timeout=5)
    assert resp.to_status is StageStatus.in_progress
    assert resp.run_id

    # And the stage is durably `in_progress`, so a reloaded page sees "building", not "Not started".
    stages = await ProjectService().list_stages(pid, user)
    assert next(s for s in stages if s.stage is Stage.build).status is StageStatus.in_progress

    codegen.release.set()
    await drain_detached()

    stages = await ProjectService().list_stages(pid, user)
    assert next(s for s in stages if s.stage is Stage.build).status is StageStatus.complete


async def test_a_running_build_is_discoverable_after_a_refresh(restore_registry: None) -> None:
    user, pid = await _project()
    codegen = SlowCodegen()
    register_handler(Stage.build, _handler(codegen))

    await Conductor().handle_intent(
        user, Intent(project_id=pid, stage=Stage.build, action=IntentAction.proceed)
    )
    await asyncio.wait_for(codegen.started.wait(), timeout=5)

    # What the reattach endpoint reads: an open run carrying live progress. A refreshed client has
    # no other way to find this — its realtime backlog may long since have been evicted.
    active = await RunRepo().active_for_project(pid, BUILD_RUN_KINDS)
    assert active, "a running build must leave an open Run to reattach to"
    codegen_run = next(r for r in active if r.kind == "codegen:build")
    reloaded = await Run.get(codegen_run.id)
    assert reloaded is not None
    assert reloaded.progress.step == "implement"
    assert reloaded.progress.label == "Installing dependencies"
    assert reloaded.progress.files == ["backend/src/features/todo/todo.model.ts"]

    codegen.release.set()
    await drain_detached()

    # Once finished, nothing is in flight — the UI must stop showing a spinner.
    assert await RunRepo().active_for_project(pid, BUILD_RUN_KINDS) == []


async def test_a_second_build_is_refused_while_one_runs(restore_registry: None) -> None:
    user, pid = await _project()
    codegen = SlowCodegen()
    register_handler(Stage.build, _handler(codegen))

    await Conductor().handle_intent(
        user, Intent(project_id=pid, stage=Stage.build, action=IntentAction.proceed)
    )
    await asyncio.wait_for(codegen.started.wait(), timeout=5)

    # Two builds share one workspace and would race on the same files.
    with pytest.raises(UserError, match="already running"):
        await Conductor().handle_intent(
            user, Intent(project_id=pid, stage=Stage.build, action=IntentAction.proceed)
        )

    codegen.release.set()
    await drain_detached()

    # …and the lock lifts once it is done.
    await Conductor().handle_intent(
        user, Intent(project_id=pid, stage=Stage.build, action=IntentAction.proceed)
    )
    codegen.release.set()
    await drain_detached()


async def test_orphaned_runs_are_reaped_at_startup(restore_registry: None) -> None:
    """A build run left open by a dead process must not lock the project out forever."""
    user, pid = await _project()
    orphan = await Run(project_id=pid, kind="codegen:build").insert()
    orphan.progress.step = "implement"
    await orphan.save()

    assert await RunRepo().active_for_project(pid, BUILD_RUN_KINDS) != []

    reaped = await reap_orphaned_runs()

    assert reaped == 1
    assert await RunRepo().active_for_project(pid, BUILD_RUN_KINDS) == []
    reloaded = await Run.get(orphan.id)
    assert reloaded is not None and reloaded.finished_at is not None
    # Reported as interrupted rather than silently "done", so the UI does not imply it succeeded.
    assert reloaded.progress.step == "failed"

    # And a new build is accepted again.
    register_handler(Stage.build, _handler(SlowCodegen()))
    resp = await Conductor().handle_intent(
        user, Intent(project_id=pid, stage=Stage.build, action=IntentAction.proceed)
    )
    assert resp.to_status is StageStatus.in_progress


class ExplodingCodegen:
    async def run(self, project: Project, run: Run, **kwargs: object) -> BuildReport:
        raise RuntimeError("provider exploded")


async def test_a_failed_detached_build_releases_the_stage(restore_registry: None) -> None:
    user, pid = await _project()
    register_handler(Stage.build, _handler(ExplodingCodegen()))

    await Conductor().handle_intent(
        user, Intent(project_id=pid, stage=Stage.build, action=IntentAction.proceed)
    )
    await drain_detached()

    # Left `in_progress`, the UI would spin forever and the project could never build again.
    stages = await ProjectService().list_stages(pid, user)
    assert next(s for s in stages if s.stage is Stage.build).status is StageStatus.awaiting_user
    assert await RunRepo().active_for_project(pid, BUILD_RUN_KINDS) == []
