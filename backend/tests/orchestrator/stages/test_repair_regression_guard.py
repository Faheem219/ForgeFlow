"""Regression guard (phase-31): two patches that break previously-passing tests stop the loop."""

from __future__ import annotations

import pytest
from beanie import PydanticObjectId

from app.db.models import Run
from app.db.models.enums import RepairOutcome
from app.orchestrator.stages.repair import (
    LOOP_ESCALATED,
    REASON_REGRESSED,
    RepairLoopController,
)
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


async def test_two_regressions_stop_the_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    # A high stall threshold isolates the regression guard from the stall guard.
    configure_loop(monkeypatch, max_iterations=5, stall_threshold=99)
    project = await make_project()
    run = await Run(project_id=project.id or PydanticObjectId(), kind="repair:loop").insert()
    red = await make_test_run(project, [failing("a"), failing("b")])

    agent = ScriptedAgent(
        [
            # Fixes `a` but breaks `c`.
            await scripted_result(
                project, run, 1, failing_names=["b", "c"], newly_failing=["c"], newly_passing=["a"]
            ),
            # Fixes `b` but breaks `d` — twice is a pattern, not bad luck.
            await scripted_result(
                project, run, 2, failing_names=["c", "d"], newly_failing=["d"], newly_passing=["b"]
            ),
        ]
    )

    result = await RepairLoopController(agent=agent, analyzer=StubAnalyzer()).run(
        project, red, run=run
    )

    assert agent.iterations == [1, 2]  # stopped on the second regression
    assert result.outcome == LOOP_ESCALATED
    assert result.escalation is not None and result.escalation.reason == REASON_REGRESSED
    assert result.metrics.regressions_introduced == 2
    assert "breaking tests that were passing" in result.escalation.summary
    assert "2 attempt(s) broke previously-passing tests" in result.escalation.summary


async def test_a_regressing_attempt_is_recorded_as_regressed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression dominates the per-attempt verdict, even when the attempt also fixed something."""
    configure_loop(monkeypatch, max_iterations=5, stall_threshold=99)
    project = await make_project()
    run = await Run(project_id=project.id or PydanticObjectId(), kind="repair:loop").insert()
    red = await make_test_run(project, [failing("a"), failing("b")])

    agent = ScriptedAgent(
        [
            # Net progress (2 → 1 failing) but it broke something: still `regressed`.
            await scripted_result(
                project, run, 1, failing_names=["c"], newly_failing=["c"], newly_passing=["a", "b"]
            ),
            await scripted_result(project, run, 2, failing_names=[], newly_passing=["c"]),
        ]
    )

    result = await RepairLoopController(agent=agent, analyzer=StubAnalyzer()).run(
        project, red, run=run
    )

    assert [a.outcome for a in result.attempts] == [RepairOutcome.regressed, RepairOutcome.fixed]
    # One regression is tolerated — the loop kept going and converged.
    assert result.fixed is True
    assert result.metrics.regressions_introduced == 1
