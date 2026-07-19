"""Audit trail (phase-31): every iteration is a RepairAttempt, the loop is a costed Run, the
convergence metrics are recorded, and the whole report is persisted + streamed."""

from __future__ import annotations

import json

import pytest
from beanie import PydanticObjectId

from app.db.models import RepairAttempt, Run
from app.db.models.enums import ArtifactType, Stage
from app.orchestrator.artifacts import ArtifactService
from app.orchestrator.stages.repair import (
    LOOP_ESCALATED,
    REPAIR_REPORT_KIND,
    RepairLoopController,
)
from app.realtime.hub import get_hub
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


async def test_every_iteration_is_recorded_as_an_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_loop(monkeypatch, max_iterations=5, stall_threshold=2)
    project = await make_project()
    assert project.id is not None
    run = await Run(project_id=project.id, kind="repair:loop").insert()
    red = await make_test_run(project, [failing("a"), failing("b")])

    agent = ScriptedAgent(
        [
            await scripted_result(project, run, 1, failing_names=["b"], newly_passing=["a"]),
            await scripted_result(project, run, 2, failing_names=[], newly_passing=["b"]),
        ]
    )
    result = await RepairLoopController(agent=agent, analyzer=StubAnalyzer()).run(
        project, red, run=run
    )

    stored = await RepairAttempt.find({"project_id": project.id}).sort("+iteration").to_list()
    assert [a.iteration for a in stored] == [1, 2]
    assert all(a.outcome is not None for a in stored)  # the controller judges every attempt
    assert all(a.diff_ref for a in stored)  # each patch's diff is retrievable
    assert all(a.resulting_run_id is not None for a in stored)
    assert [a.iteration for a in result.attempts] == [1, 2]


async def test_the_loop_report_is_persisted_for_the_ui_and_eval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_loop(monkeypatch, max_iterations=2, stall_threshold=99)
    project = await make_project()
    assert project.id is not None
    run = await Run(project_id=project.id, kind="repair:loop").insert()
    red = await make_test_run(project, [failing("a")])

    agent = ScriptedAgent([await scripted_result(project, run, 1, failing_names=["a"])])
    result = await RepairLoopController(agent=agent, analyzer=StubAnalyzer()).run(
        project, red, run=run
    )

    artifacts = ArtifactService()
    latest = await artifacts.get_latest(project.id, Stage.test, ArtifactType.repair_attempt)
    assert latest is not None
    assert latest.meta["kind"] == REPAIR_REPORT_KIND
    assert latest.meta["outcome"] == LOOP_ESCALATED
    assert latest.meta["iterations"] == 2

    body = json.loads(await artifacts.get_content(latest) or "{}")
    assert body["outcome"] == LOOP_ESCALATED
    assert body["metrics"]["failing_by_iteration"] == [1, 1]
    assert body["escalation"]["reason"] == result.escalation.reason  # type: ignore[union-attr]
    assert len(body["attempts"]) == 2


async def test_metrics_capture_convergence_and_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_loop(monkeypatch, max_iterations=5, stall_threshold=2)
    project = await make_project()
    run = await Run(project_id=project.id or PydanticObjectId(), kind="repair:loop").insert()
    run.cost.tokens = 1234
    run.cost.inr = 4.56
    await run.save()

    red = await make_test_run(project, [failing("a"), failing("b"), passing("ok")])
    agent = ScriptedAgent(
        [
            await scripted_result(project, run, 1, failing_names=["b"], newly_passing=["a"]),
            await scripted_result(project, run, 2, failing_names=[], newly_passing=["b"]),
        ]
    )
    result = await RepairLoopController(agent=agent, analyzer=StubAnalyzer()).run(
        project, red, run=run
    )

    metrics = result.metrics.to_dict()
    assert metrics["initial_failing"] == 2
    assert metrics["failing_by_iteration"] == [1, 0]
    assert metrics["final_failing"] == 0
    assert metrics["iterations"] == 2
    assert metrics["tokens_spent"] == 1234  # read off the loop's Run
    assert metrics["cost_inr"] == 4.56
    assert metrics["wall_clock_s"] >= 0


async def test_each_iteration_and_the_terminal_state_are_streamed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_loop(monkeypatch, max_iterations=2, stall_threshold=99)
    project = await make_project()
    assert project.id is not None
    run = await Run(project_id=project.id, kind="repair:loop").insert()
    red = await make_test_run(project, [failing("a")])
    agent = ScriptedAgent([await scripted_result(project, run, 1, failing_names=["a"])])

    hub = get_hub()
    async with hub.subscription(str(project.id)) as queue:
        await RepairLoopController(agent=agent, analyzer=StubAnalyzer()).run(project, red, run=run)
        events = []
        while not queue.empty():
            events.append(queue.get_nowait())

    kinds = [e.event for e in events]
    assert kinds.count("repair.iteration") == 2
    assert kinds[-1] == "repair.escalation"  # terminal event last

    first = next(e for e in events if e.event == "repair.iteration")
    assert set(first.payload) >= {"i", "failing_count", "regressions", "tokens"}
    assert first.payload["i"] == 1
