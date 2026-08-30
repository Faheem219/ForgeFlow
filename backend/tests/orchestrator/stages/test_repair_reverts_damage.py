"""The loop may spend iterations; it may not hand back an app worse than it found.

A repair attempt is committed as it goes, and nothing used to undo one. So a patch that broke a
passing test and fixed nothing stayed in the workspace, and a loop that then escalated left the
user with an app *more* broken than before they pressed Repair.

The line drawn here is deliberately narrow. An attempt that breaks one test while fixing two is
still progress — the regression guard counts it and stops at ``repair_max_regressions``, and that
tolerance is load-bearing (see ``test_repair_regression_guard``). Only **strictly worse** attempts
— regressed, and the failing set did not shrink — are pure damage, and only those are undone.
"""

from __future__ import annotations

import pytest
from beanie import PydanticObjectId

from app.db.models import RepairAttempt, Run
from app.db.models.enums import RepairOutcome
from app.orchestrator.stages.repair import RepairLoopController
from tests.orchestrator.stages.repair_loop_fakes import (
    FakeRevertableWorkspace,
    ScriptedAgent,
    StubAnalyzer,
    configure_loop,
    failing,
    make_project,
    make_test_run,
    scripted_result,
)

pytestmark = pytest.mark.usefixtures("mongo_db")


async def test_a_patch_that_only_broke_things_is_undone(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_loop(monkeypatch, max_iterations=1, stall_threshold=99)
    project = await make_project()
    run = await Run(project_id=project.id or PydanticObjectId(), kind="repair:loop").insert()
    red = await make_test_run(project, [failing("a"), failing("b")])
    workspace = FakeRevertableWorkspace()

    agent = ScriptedAgent(
        [
            # Broke `c`, fixed nothing: 2 failing before, 3 after. Pure damage.
            await scripted_result(
                project, run, 1, failing_names=["a", "b", "c"], newly_failing=["c"]
            ),
        ]
    )

    result = await RepairLoopController(
        agent=agent, analyzer=StubAnalyzer(), workspace=workspace
    ).run(project, red, run=run)

    # The workspace went back to the commit the attempt started from.
    assert workspace.restored == ["sha0"]
    # The attempt is still in the trail — its diff stays readable — but flagged as not applied.
    stored = await RepairAttempt.find({"project_id": project.id}).to_list()
    assert [a.reverted for a in stored] == [True]
    assert [a.outcome for a in stored] == [RepairOutcome.regressed]
    # The measured count is recorded honestly; the guard still counted the regression.
    assert result.metrics.failing_by_iteration == [3]
    assert result.metrics.regressions_introduced == 1


async def test_a_regression_that_still_made_progress_is_kept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The tolerated case must be untouched: net progress is progress, even at a cost."""
    configure_loop(monkeypatch, max_iterations=5, stall_threshold=99)
    project = await make_project()
    run = await Run(project_id=project.id or PydanticObjectId(), kind="repair:loop").insert()
    red = await make_test_run(project, [failing("a"), failing("b")])
    workspace = FakeRevertableWorkspace()

    agent = ScriptedAgent(
        [
            # Fixed `a` and `b`, broke `c`: 2 → 1 failing. Net progress.
            await scripted_result(
                project, run, 1, failing_names=["c"], newly_failing=["c"], newly_passing=["a", "b"]
            ),
            await scripted_result(project, run, 2, failing_names=[], newly_passing=["c"]),
        ]
    )

    result = await RepairLoopController(
        agent=agent, analyzer=StubAnalyzer(), workspace=workspace
    ).run(project, red, run=run)

    assert workspace.restored == []  # nothing was undone
    assert result.fixed is True  # and the loop still converged
    assert [a.reverted for a in result.attempts] == [False, False]


async def test_a_green_attempt_is_never_undone(monkeypatch: pytest.MonkeyPatch) -> None:
    """Green ends the loop — a run that passes everything is never "worse", whatever it broke."""
    configure_loop(monkeypatch, max_iterations=5, stall_threshold=99)
    project = await make_project()
    run = await Run(project_id=project.id or PydanticObjectId(), kind="repair:loop").insert()
    red = await make_test_run(project, [failing("a")])
    workspace = FakeRevertableWorkspace()

    agent = ScriptedAgent(
        [await scripted_result(project, run, 1, failing_names=[], newly_passing=["a"])]
    )

    result = await RepairLoopController(
        agent=agent, analyzer=StubAnalyzer(), workspace=workspace
    ).run(project, red, run=run)

    assert workspace.restored == []
    assert result.fixed is True


async def test_a_failed_undo_leaves_the_loop_exactly_as_it_was(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail soft: if the undo cannot run, carry on with the patch — never worse than no undo."""
    configure_loop(monkeypatch, max_iterations=1, stall_threshold=99)
    project = await make_project()
    run = await Run(project_id=project.id or PydanticObjectId(), kind="repair:loop").insert()
    red = await make_test_run(project, [failing("a"), failing("b")])
    workspace = FakeRevertableWorkspace(fails=True)

    agent = ScriptedAgent(
        [await scripted_result(project, run, 1, failing_names=["a", "b", "c"], newly_failing=["c"])]
    )

    result = await RepairLoopController(
        agent=agent, analyzer=StubAnalyzer(), workspace=workspace
    ).run(project, red, run=run)

    assert workspace.restored == ["sha0"]  # it was attempted
    stored = await RepairAttempt.find({"project_id": project.id}).to_list()
    assert [a.reverted for a in stored] == [False]  # but not claimed in the trail
    assert result.escalation is not None  # and the loop ended normally
