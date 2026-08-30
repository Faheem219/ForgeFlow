"""An attempt that writes nothing is its own terminal signal (phase-63).

The reported session: the agent was handed a file that could not contain the bug, said so in plain
English — *"the fix has to live in the component that renders the form"* — and wrote nothing. The
loop scored that as ordinary no-progress, re-assembled a byte-identical context, asked again, and
finally told the user "the failing tests stopped shrinking. I patched: no files." The model's
explanation, the one genuinely useful sentence in the run, was discarded.

So: count consecutive no-op attempts, stop at ``repair_max_noop_attempts``, and escalate with the
agent's own words.
"""

from __future__ import annotations

import pytest
from beanie import PydanticObjectId

from app.db.models import Run
from app.db.models.enums import Stage, StageStatus
from app.db.repos import StageStateRepo
from app.orchestrator.stages.repair import (
    LOOP_ESCALATED,
    LOOP_FIXED,
    REASON_NO_PATCH,
    REASON_STALLED,
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

EXPLANATION = (
    "I cannot fix this from main.tsx alone — it renders no form. The fix has to live in the "
    "component that renders the label."
)


async def test_two_no_op_attempts_escalate_with_the_agents_own_explanation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_loop(monkeypatch, max_iterations=5, stall_threshold=4, max_noop=2)
    project = await make_project()
    assert project.id is not None
    run = await Run(project_id=project.id, kind="repair:loop").insert()
    red = await make_test_run(project, [failing("a")])

    agent = ScriptedAgent(
        [
            await scripted_result(
                project, run, 1, failing_names=["a"], files_written=[], summary=EXPLANATION
            )
        ]
    )

    result = await RepairLoopController(agent=agent, analyzer=StubAnalyzer()).run(
        project, red, run=run
    )

    # Stopped at the no-op bound (2), not at the stall threshold (4) or the cap (5).
    assert agent.iterations == [1, 2]
    assert result.outcome == LOOP_ESCALATED
    assert result.escalation is not None
    assert result.escalation.reason == REASON_NO_PATCH
    assert "could not fix it from the files I was given" in result.escalation.summary
    assert "the component that renders the label" in result.escalation.summary
    assert result.metrics.noop_attempts == 2

    state = await StageStateRepo().get_or_create(project.id, Stage.test)
    assert state.status is StageStatus.awaiting_user


async def test_a_single_no_op_does_not_end_the_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """One retry absorbs sampling variance: the same context can still yield a patch."""
    configure_loop(monkeypatch, max_iterations=5, stall_threshold=4, max_noop=2)
    project = await make_project()
    run = await Run(project_id=project.id or PydanticObjectId(), kind="repair:loop").insert()
    red = await make_test_run(project, [failing("a")])

    agent = ScriptedAgent(
        [
            await scripted_result(project, run, 1, failing_names=["a"], files_written=[]),
            await scripted_result(project, run, 2, failing_names=[], newly_passing=["a"]),
        ]
    )

    result = await RepairLoopController(agent=agent, analyzer=StubAnalyzer()).run(
        project, red, run=run
    )

    assert result.outcome == LOOP_FIXED
    assert agent.iterations == [1, 2]
    assert result.metrics.noop_attempts == 1  # counted for the record, not acted on


async def test_the_counter_is_consecutive_not_cumulative(monkeypatch: pytest.MonkeyPatch) -> None:
    """A no-op, then a patch, then a no-op is not the "it cannot get there" signal."""
    configure_loop(monkeypatch, max_iterations=4, stall_threshold=4, max_noop=2)
    project = await make_project()
    run = await Run(project_id=project.id or PydanticObjectId(), kind="repair:loop").insert()
    red = await make_test_run(project, [failing("a"), failing("b")])

    agent = ScriptedAgent(
        [
            await scripted_result(project, run, 1, failing_names=["a", "b"], files_written=[]),
            # A real patch: the failing set shrinks, so the no-op streak resets.
            await scripted_result(project, run, 2, failing_names=["a"], newly_passing=["b"]),
            await scripted_result(project, run, 3, failing_names=["a"], files_written=[]),
            await scripted_result(project, run, 4, failing_names=["a"], files_written=[]),
        ]
    )

    result = await RepairLoopController(agent=agent, analyzer=StubAnalyzer()).run(
        project, red, run=run
    )

    assert agent.iterations == [1, 2, 3, 4]  # ran on to the fourth, then the streak hit 2
    assert result.escalation is not None and result.escalation.reason == REASON_NO_PATCH
    assert result.metrics.noop_attempts == 3


async def test_a_patching_attempt_that_stops_shrinking_still_stalls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The no-op guard narrows the diagnosis; it must not swallow the stall case."""
    configure_loop(monkeypatch, max_iterations=5, stall_threshold=2, max_noop=2)
    project = await make_project()
    run = await Run(project_id=project.id or PydanticObjectId(), kind="repair:loop").insert()
    red = await make_test_run(project, [failing("a")])

    agent = ScriptedAgent([await scripted_result(project, run, 1, failing_names=["a"])])

    result = await RepairLoopController(agent=agent, analyzer=StubAnalyzer()).run(
        project, red, run=run
    )

    assert result.escalation is not None and result.escalation.reason == REASON_STALLED
    assert result.metrics.noop_attempts == 0
