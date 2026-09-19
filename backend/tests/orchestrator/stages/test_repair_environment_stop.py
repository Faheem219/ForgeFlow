"""A failure the sandbox causes stops the loop before any attempt (phase-65).

The reported session: a model spent its repair attempts discovering, and re-discovering, that it
could not download a ``mongod`` because the sandbox has no network. No patch can supply a binary
the image lacks, so the loop now recognises it from the context and escalates at zero iterations —
no attempt, no ``MODEL_CODEGEN`` tokens — saying what does fix it. A *repairable* offline finding
(a test calling the internet) must not trigger this: the agent can fix that, and is told how.
"""

from __future__ import annotations

import pytest

from app.agents.repair_context import RepairContext
from app.db.models import Project, Run, TestRun
from app.db.models.enums import Stage, StageStatus
from app.db.repos import StageStateRepo
from app.orchestrator.stages.repair import (
    LOOP_ESCALATED,
    LOOP_FIXED,
    REASON_ENVIRONMENT,
    RepairLoopController,
)
from app.sandbox.offline import diagnose_offline
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

DB_UNAVAILABLE = "[forgeflow:test-db-unavailable] The in-memory MongoDB could not start"


class OfflineAnalyzer(StubAnalyzer):
    """The stub context, plus what the real analyzer would have found in the failure output."""

    def __init__(self, failure_text: str) -> None:
        super().__init__()
        self._diagnosis = diagnose_offline(failure_text)

    async def analyze(
        self, project: Project, test_run: TestRun, *, persist: bool = True
    ) -> RepairContext:
        context = await super().analyze(project, test_run, persist=persist)
        if self._diagnosis is not None and not self._diagnosis.repairable:
            context.environment = self._diagnosis
        elif self._diagnosis is not None:
            context.sandbox_hints.append(self._diagnosis.hint)
        return context


async def test_a_missing_test_database_escalates_before_any_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_loop(monkeypatch, max_iterations=5)
    project = await make_project()
    assert project.id is not None
    run = await Run(project_id=project.id, kind="repair:loop").insert()
    red = await make_test_run(project, [failing("a"), failing("b")])
    agent = ScriptedAgent([await scripted_result(project, run, 1, failing_names=[])])

    result = await RepairLoopController(agent=agent, analyzer=OfflineAnalyzer(DB_UNAVAILABLE)).run(
        project, red, run=run
    )

    assert agent.iterations == []  # the agent was never asked — no tokens spent
    assert result.outcome == LOOP_ESCALATED
    assert result.escalation is not None
    assert result.escalation.reason == REASON_ENVIRONMENT
    assert result.metrics.iterations == 0
    summary = result.escalation.summary
    assert "the sandbox environment is broken, not the code" in summary
    assert "make sandbox-build" in summary  # what fixes it…
    assert summary.endswith("Fix the environment, then run the tests again.")  # …not "guide me"
    assert len(result.escalation.failing_tests) == 2

    state = await StageStateRepo().get_or_create(project.id, Stage.test)
    assert state.status is StageStatus.awaiting_user


async def test_a_repairable_offline_finding_still_reaches_the_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A test calling the internet is the code's problem: the agent fixes it (with a hint)."""
    configure_loop(monkeypatch, max_iterations=5)
    project = await make_project()
    assert project.id is not None
    run = await Run(project_id=project.id, kind="repair:loop").insert()
    red = await make_test_run(project, [failing("a")])
    agent = ScriptedAgent(
        [await scripted_result(project, run, 1, failing_names=[], newly_passing=["a"])]
    )

    result = await RepairLoopController(
        agent=agent, analyzer=OfflineAnalyzer("getaddrinfo ENOTFOUND api.stripe.com")
    ).run(project, red, run=run)

    assert agent.iterations == [1]
    assert result.outcome == LOOP_FIXED
