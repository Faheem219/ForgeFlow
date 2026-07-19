"""Stall → escalation (phase-31): a non-shrinking failing set stops at the threshold — not the
max — and hands a human a "here's where I'm stuck" payload."""

from __future__ import annotations

import pytest
from beanie import PydanticObjectId

from app.agents.repair import RepairResult
from app.core.errors import UserError
from app.db.models import Run
from app.db.models.enums import RepairOutcome, Stage, StageStatus
from app.db.repos import StageStateRepo
from app.orchestrator.stages.repair import (
    LOOP_ESCALATED,
    REASON_BLOCKED,
    REASON_BUDGET,
    REASON_STALLED,
    RESUME_ACTION,
    RepairLoopController,
)
from tests.orchestrator.stages.repair_loop_fakes import (
    RaisingAgent,
    ScriptedAgent,
    StubAnalyzer,
    configure_loop,
    failing,
    make_project,
    make_test_run,
    scripted_result,
)

pytestmark = pytest.mark.usefixtures("mongo_db")


async def test_stalls_at_the_threshold_not_the_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_loop(monkeypatch, max_iterations=5, stall_threshold=2)
    project = await make_project()
    assert project.id is not None
    run = await Run(project_id=project.id, kind="repair:loop").insert()
    red = await make_test_run(project, [failing("a"), failing("b")])

    # Every attempt leaves the same two failures — no shrink, ever.
    agent = ScriptedAgent([await scripted_result(project, run, 1, failing_names=["a", "b"])])

    result = await RepairLoopController(agent=agent, analyzer=StubAnalyzer()).run(
        project, red, run=run
    )

    # Stopped at the stall threshold (2), well short of max_iterations (5).
    assert agent.iterations == [1, 2]
    assert result.outcome == LOOP_ESCALATED
    assert result.escalation is not None
    assert result.escalation.reason == REASON_STALLED
    assert result.metrics.failing_by_iteration == [2, 2]
    assert [a.outcome for a in result.attempts] == [
        RepairOutcome.no_progress,
        RepairOutcome.no_progress,
    ]

    # The payload is actually actionable.
    esc = result.escalation
    assert {t["name"] for t in esc.failing_tests} == {"a", "b"}
    assert len(esc.diffs_tried) == 2
    assert all(d["diff_ref"] for d in esc.diffs_tried)
    assert "stopped after 2 attempt(s)" in esc.summary
    assert "stopped shrinking" in esc.summary
    assert esc.resume == RESUME_ACTION  # resume-with-guidance re-enters Build
    assert esc.resume["stage"] == str(Stage.build) and esc.resume["action"] == "refine"

    # Escalation is a terminal state that hands the stage back to the user.
    state = await StageStateRepo().get_or_create(project.id, Stage.test)
    assert state.status is StageStatus.awaiting_user

    reloaded = await Run.get(run.id)
    assert reloaded is not None and reloaded.outcome == LOOP_ESCALATED


async def test_an_oscillating_failing_set_counts_as_no_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same size, different members — swapping failures is not converging."""
    configure_loop(monkeypatch, max_iterations=5, stall_threshold=2)
    project = await make_project()
    run = await Run(project_id=project.id or PydanticObjectId(), kind="repair:loop").insert()
    red = await make_test_run(project, [failing("a"), failing("b")])

    agent = ScriptedAgent(
        [
            await scripted_result(project, run, 1, failing_names=["b", "c"]),
            await scripted_result(project, run, 2, failing_names=["c", "d"]),
        ]
    )

    result = await RepairLoopController(agent=agent, analyzer=StubAnalyzer()).run(
        project, red, run=run
    )

    assert result.escalation is not None and result.escalation.reason == REASON_STALLED
    assert result.metrics.failing_by_iteration == [2, 2]


async def test_a_budget_halt_escalates_rather_than_crashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_loop(monkeypatch, max_iterations=5, stall_threshold=2)
    project = await make_project()
    assert project.id is not None
    run = await Run(project_id=project.id, kind="repair:loop").insert()
    red = await make_test_run(project, [failing("a")])

    agent = RaisingAgent(UserError("Budget cap reached (project: ₹10.00 of ₹10.00)."))
    result = await RepairLoopController(agent=agent, analyzer=StubAnalyzer()).run(
        project, red, run=run
    )

    assert result.outcome == LOOP_ESCALATED
    assert result.escalation is not None and result.escalation.reason == REASON_BUDGET
    assert "Budget cap reached" in result.escalation.summary
    state = await StageStateRepo().get_or_create(project.id, Stage.test)
    assert state.status is StageStatus.awaiting_user


async def test_nothing_safe_to_patch_escalates(monkeypatch: pytest.MonkeyPatch) -> None:
    """The agent's read-only-oracle guard (phase-30) surfaces as a `blocked` escalation."""
    configure_loop(monkeypatch, max_iterations=5, stall_threshold=2)
    project = await make_project()
    run = await Run(project_id=project.id or PydanticObjectId(), kind="repair:loop").insert()
    red = await make_test_run(project, [failing("a")])

    blocked = RepairResult(note="No editable source files — this needs a human.")
    agent = ScriptedAgent([blocked])

    result = await RepairLoopController(agent=agent, analyzer=StubAnalyzer()).run(
        project, red, run=run
    )

    assert result.escalation is not None and result.escalation.reason == REASON_BLOCKED
    assert "needs a human" in result.escalation.summary
    assert result.attempts == []  # no attempt was recorded — nothing was tried
