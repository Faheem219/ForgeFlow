"""Convergence (phase-31): a fixable bug goes green within the cap and records ``outcome=fixed``."""

from __future__ import annotations

import pytest
from beanie import PydanticObjectId

from app.db.models import Run
from app.db.models.enums import RepairOutcome, Stage, StageStatus
from app.db.repos import StageStateRepo
from app.orchestrator.stages.repair import LOOP_FIXED, RepairLoopController
from tests.orchestrator.stages.repair_loop_fakes import (
    ScriptedAgent,
    StubAnalyzer,
    configure_loop,
    failing,
    make_project,
    make_test_run,
    passing,
    scripted_result,
)

pytestmark = pytest.mark.usefixtures("mongo_db")


async def test_loop_converges_and_stops_as_soon_as_it_is_green(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_loop(monkeypatch, max_iterations=5, stall_threshold=2)
    project = await make_project()
    run = await Run(project_id=project.id or PydanticObjectId(), kind="repair:loop").insert()

    red = await make_test_run(project, [failing("a"), failing("b"), passing("ok")])
    agent = ScriptedAgent(
        [
            # Attempt 1 fixes one of two failures (progress, not yet green).
            await scripted_result(
                project, run, 1, failing_names=["b"], passing_names=["a", "ok"], newly_passing=["a"]
            ),
            # Attempt 2 fixes the rest.
            await scripted_result(
                project,
                run,
                2,
                failing_names=[],
                passing_names=["a", "b", "ok"],
                newly_passing=["b"],
            ),
        ]
    )

    result = await RepairLoopController(agent=agent, analyzer=StubAnalyzer()).run(
        project, red, run=run
    )

    assert result.outcome == LOOP_FIXED and result.fixed is True
    assert agent.iterations == [1, 2]  # stopped the moment it went green — no wasted iteration
    assert result.metrics.initial_failing == 2
    assert result.metrics.failing_by_iteration == [1, 0]
    assert result.metrics.final_failing == 0
    assert result.metrics.regressions_introduced == 0
    assert result.escalation is None

    # Both attempts made progress, so both are `fixed`.
    assert [a.outcome for a in result.attempts] == [RepairOutcome.fixed, RepairOutcome.fixed]

    # The loop is the eval unit: its Run carries the terminal outcome.
    reloaded = await Run.get(run.id)
    assert reloaded is not None
    assert reloaded.outcome == LOOP_FIXED and reloaded.finished_at is not None


async def test_an_already_green_run_is_a_no_op(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_loop(monkeypatch, max_iterations=5, stall_threshold=2)
    project = await make_project()
    run = await Run(project_id=project.id or PydanticObjectId(), kind="repair:loop").insert()
    green = await make_test_run(project, [passing("ok")])

    agent = ScriptedAgent([])
    result = await RepairLoopController(agent=agent, analyzer=StubAnalyzer()).run(
        project, green, run=run
    )

    assert result.outcome == LOOP_FIXED
    assert agent.iterations == []  # the agent was never invoked — nothing to repair
    assert result.metrics.iterations == 0


async def test_convergence_does_not_touch_the_stage_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only escalation hands the stage back to the user; a green loop leaves it to the caller."""
    configure_loop(monkeypatch, max_iterations=5, stall_threshold=2)
    project = await make_project()
    assert project.id is not None
    run = await Run(project_id=project.id, kind="repair:loop").insert()
    red = await make_test_run(project, [failing("a")])

    agent = ScriptedAgent(
        [await scripted_result(project, run, 1, failing_names=[], passing_names=["a"])]
    )
    await RepairLoopController(agent=agent, analyzer=StubAnalyzer()).run(project, red, run=run)

    state = await StageStateRepo().get_or_create(project.id, Stage.test)
    assert state.status is not StageStatus.awaiting_user
