"""The repair loop serves both Test and Build via three defaulted kwargs (phase-55 task 8).

Build reuses the *same* bounded controller rather than a second loop (D7 / Golden Rule 5). Two
things must hold: constructed for Build (`stage=Stage.build, owns_stage_status=False`) it emits
under `build`, persists its audit trail under `(build, repair_attempt)`, never touches the Test
stage status (the conductor owns Build's); and constructed with **no** kwargs it behaves as the
Test stage always has — the parameterization regression guard.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.db.models import Run
from app.db.models.enums import ArtifactType, Stage, StageStatus
from app.db.repos import StageStateRepo
from app.orchestrator.artifacts import ArtifactService
from app.orchestrator.stages.repair import LOOP_ESCALATED, RepairLoopController
from tests.orchestrator.stages.repair_loop_fakes import (
    ScriptedAgent,
    StubAnalyzer,
    configure_loop,
    failing,
    make_project,
    make_test_run,
    scripted_result,
)

pytestmark = pytest.mark.usefixtures("mongo_db")


def _capture_emit(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, Any]]:
    """Record (event, stage) for every emit the controller makes."""
    seen: list[tuple[Any, Any]] = []

    async def fake_emit(*args: Any, **kwargs: Any) -> None:
        event = args[1] if len(args) > 1 else None
        seen.append((event, kwargs.get("stage")))

    monkeypatch.setattr("app.orchestrator.stages.repair.emit", fake_emit)
    return seen


async def test_build_loop_emits_build_persists_under_build_and_spares_test(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_loop(monkeypatch, max_iterations=5, stall_threshold=2)
    events = _capture_emit(monkeypatch)
    project = await make_project()
    assert project.id is not None
    run = await Run(project_id=project.id, kind="repair:loop").insert()
    red = await make_test_run(project, [failing("a"), failing("b")])
    agent = ScriptedAgent([await scripted_result(project, run, 1, failing_names=["a", "b"])])

    result = await RepairLoopController(
        agent=agent,
        analyzer=StubAnalyzer(),
        stage=Stage.build,
        owns_stage_status=False,
    ).run(project, red, run=run)

    assert result.outcome == LOOP_ESCALATED
    # Every emit is tagged stage=build, none stage=test.
    stages = {stage for _, stage in events if stage is not None}
    assert stages == {Stage.build}
    # The loop did NOT set any stage status (Build's conductor owns it via StageResult.next_status).
    assert (
        await StageStateRepo().get_or_create(project.id, Stage.test)
    ).status is StageStatus.empty
    assert (
        await StageStateRepo().get_or_create(project.id, Stage.build)
    ).status is StageStatus.empty
    # The audit trail lands under (build, repair_attempt) — a distinct trail from the Test stage's.
    artifacts = ArtifactService()
    assert (
        await artifacts.get_latest(project.id, Stage.build, ArtifactType.repair_attempt) is not None
    )
    assert await artifacts.get_latest(project.id, Stage.test, ArtifactType.repair_attempt) is None


async def test_default_construction_is_the_test_stage_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No kwargs → the Test stage behaves exactly as before this phase."""
    configure_loop(monkeypatch, max_iterations=5, stall_threshold=2)
    events = _capture_emit(monkeypatch)
    project = await make_project()
    assert project.id is not None
    run = await Run(project_id=project.id, kind="repair:loop").insert()
    red = await make_test_run(project, [failing("a"), failing("b")])
    agent = ScriptedAgent([await scripted_result(project, run, 1, failing_names=["a", "b"])])

    result = await RepairLoopController(agent=agent, analyzer=StubAnalyzer()).run(
        project, red, run=run
    )

    assert result.outcome == LOOP_ESCALATED
    assert {stage for _, stage in events if stage is not None} == {Stage.test}
    # Test owns its status: escalation hands the stage back to the user.
    assert (
        await StageStateRepo().get_or_create(project.id, Stage.test)
    ).status is StageStatus.awaiting_user
    artifacts = ArtifactService()
    assert (
        await artifacts.get_latest(project.id, Stage.test, ArtifactType.repair_attempt) is not None
    )


async def test_max_iterations_override_bounds_a_single_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The per-run `max_iterations` override caps below the config default (phase-56 per phase)."""
    configure_loop(monkeypatch, max_iterations=5, stall_threshold=2)
    project = await make_project()
    assert project.id is not None
    run = await Run(project_id=project.id, kind="repair:loop").insert()
    red = await make_test_run(project, [failing("a"), failing("b")])
    # Never shrinks → without the override it would run to the stall threshold; the override caps.
    agent = ScriptedAgent([await scripted_result(project, run, 1, failing_names=["a", "b"])])

    result = await RepairLoopController(agent=agent, analyzer=StubAnalyzer()).run(
        project, red, run=run, max_iterations=1
    )

    assert agent.iterations == [1]  # exactly one iteration, the override
    assert result.outcome == LOOP_ESCALATED
