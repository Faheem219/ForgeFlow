"""The hard bound (phase-31): the loop never exceeds ``max_iterations`` and never runs unbounded."""

from __future__ import annotations

import asyncio

import pytest
from beanie import PydanticObjectId

from app.db.models import Run
from app.orchestrator.stages.repair import (
    LOOP_ESCALATED,
    REASON_CANCELLED,
    REASON_CAP,
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


async def test_never_exceeds_max_iterations(monkeypatch: pytest.MonkeyPatch) -> None:
    # A very high stall threshold isolates the cap: only the bound can stop this loop.
    configure_loop(monkeypatch, max_iterations=3, stall_threshold=99)
    project = await make_project()
    run = await Run(project_id=project.id or PydanticObjectId(), kind="repair:loop").insert()
    red = await make_test_run(project, [failing("a")])

    # Always shrinks-by-nothing but never regresses: without a cap this would spin forever.
    agent = ScriptedAgent([await scripted_result(project, run, 1, failing_names=["a"])])

    result = await RepairLoopController(agent=agent, analyzer=StubAnalyzer()).run(
        project, red, run=run
    )

    assert agent.iterations == [1, 2, 3]  # exactly the cap, never more
    assert result.metrics.iterations == 3
    assert result.outcome == LOOP_ESCALATED
    assert result.escalation is not None and result.escalation.reason == REASON_CAP
    assert "hit the iteration cap" in result.escalation.summary


async def test_a_cap_of_one_still_runs_exactly_once(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_loop(monkeypatch, max_iterations=1, stall_threshold=99)
    project = await make_project()
    run = await Run(project_id=project.id or PydanticObjectId(), kind="repair:loop").insert()
    red = await make_test_run(project, [failing("a")])

    agent = ScriptedAgent([await scripted_result(project, run, 1, failing_names=["a"])])
    result = await RepairLoopController(agent=agent, analyzer=StubAnalyzer()).run(
        project, red, run=run
    )

    assert agent.iterations == [1]
    assert result.escalation is not None and result.escalation.reason == REASON_CAP


async def test_the_loop_is_cancellable(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_loop(monkeypatch, max_iterations=5, stall_threshold=99)
    project = await make_project()
    run = await Run(project_id=project.id or PydanticObjectId(), kind="repair:loop").insert()
    red = await make_test_run(project, [failing("a")])

    cancel = asyncio.Event()
    cancel.set()  # cancelled before the first iteration
    agent = ScriptedAgent([await scripted_result(project, run, 1, failing_names=["a"])])

    result = await RepairLoopController(agent=agent, analyzer=StubAnalyzer()).run(
        project, red, run=run, cancel=cancel
    )

    assert agent.iterations == []  # never started patching
    assert result.escalation is not None and result.escalation.reason == REASON_CANCELLED


async def test_thresholds_come_from_config_so_they_are_tunable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bounds are config-resolved (admin > env > default) for the dashboard + eval sweeps."""
    configure_loop(monkeypatch, max_iterations=2, stall_threshold=99)
    project = await make_project()
    run = await Run(project_id=project.id or PydanticObjectId(), kind="repair:loop").insert()
    red = await make_test_run(project, [failing("a")])

    agent = ScriptedAgent([await scripted_result(project, run, 1, failing_names=["a"])])
    await RepairLoopController(agent=agent, analyzer=StubAnalyzer()).run(project, red, run=run)
    assert agent.iterations == [1, 2]

    # Raising the cap changes the bound with no code change.
    configure_loop(monkeypatch, max_iterations=4, stall_threshold=99)
    agent2 = ScriptedAgent([await scripted_result(project, run, 1, failing_names=["a"])])
    await RepairLoopController(agent=agent2, analyzer=StubAnalyzer()).run(project, red, run=run)
    assert agent2.iterations == [1, 2, 3, 4]
