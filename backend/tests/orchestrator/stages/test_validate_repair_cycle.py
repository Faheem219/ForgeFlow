"""The live-failure loop (phase-40): a failing site is repaired, redeployed and re-validated.

This is D12 in its sharpest form — a live failure re-enters Build instead of being a dead end.
"""

from __future__ import annotations

import pytest

from app.orchestrator.stages.validate import (
    DIAGNOSIS_CODE,
    OUTCOME_VALIDATED,
    LiveValidationController,
)
from tests.orchestrator.stages.validate_fakes import (
    CODE_FAILURE,
    FE_URL,
    PASSING,
    FakeDeployer,
    FakeLiveRunner,
    FakeRepairLoop,
    FakeRepairResult,
    RecordingEmitter,
    project_ready_to_validate,
)

pytestmark = pytest.mark.usefixtures("mongo_db")


async def test_a_live_failure_is_repaired_redeployed_and_revalidated() -> None:
    project = await project_ready_to_validate()
    live = FakeLiveRunner(CODE_FAILURE, PASSING)  # fails, then passes after the fix
    repair = FakeRepairLoop(FakeRepairResult("fixed"))
    deployer = FakeDeployer()

    report = await LiveValidationController(
        live_runner=live, repair=repair, deployer=deployer, emitter=RecordingEmitter()
    ).run(project)

    assert report.outcome == OUTCOME_VALIDATED
    assert report.url == FE_URL
    assert live.calls == 2  # validate → (repair, redeploy) → re-validate
    assert len(repair.calls) == 1
    assert deployer.calls == 1
    assert [c.index for c in report.cycles] == [1, 2]
    assert report.cycles[0].diagnosis == DIAGNOSIS_CODE
    assert report.cycles[0].repair_outcome == "fixed"
    assert report.cycles[0].redeploy_status == "live"
    assert report.cycles[1].green


async def test_the_repair_loop_is_seeded_with_the_live_run() -> None:
    """The failures the agent works from must be the *production* ones, not a stale sandbox run."""
    project = await project_ready_to_validate()
    live = FakeLiveRunner(CODE_FAILURE, PASSING)
    repair = FakeRepairLoop(FakeRepairResult("fixed"))

    report = await LiveValidationController(
        live_runner=live, repair=repair, deployer=FakeDeployer(), emitter=RecordingEmitter()
    ).run(project)

    assert repair.calls == [report.cycles[0].live_run_id]


async def test_the_cycle_streams_each_step_for_the_timeline() -> None:
    project = await project_ready_to_validate()
    emitter = RecordingEmitter()

    await LiveValidationController(
        live_runner=FakeLiveRunner(CODE_FAILURE, PASSING),
        repair=FakeRepairLoop(FakeRepairResult("fixed")),
        deployer=FakeDeployer(),
        emitter=emitter,
    ).run(project)

    assert emitter.steps() == [
        "validating",
        "diagnosed",
        "repairing",
        "redeploying",
        "validating",
        "validated",
    ]


async def test_a_repair_that_escalates_stops_the_cycle_and_carries_the_reason() -> None:
    from app.orchestrator.stages.validate import OUTCOME_ESCALATED, REASON_REPAIR
    from tests.orchestrator.stages.validate_fakes import FakeEscalation

    project = await project_ready_to_validate()
    deployer = FakeDeployer()

    report = await LiveValidationController(
        live_runner=FakeLiveRunner(CODE_FAILURE),
        repair=FakeRepairLoop(FakeRepairResult("escalated", FakeEscalation("stalled"))),
        deployer=deployer,
        emitter=RecordingEmitter(),
    ).run(project)

    assert report.outcome == OUTCOME_ESCALATED
    assert report.reason == REASON_REPAIR
    assert report.repair_escalation is not None
    assert report.repair_escalation["reason"] == "stalled"
    assert deployer.calls == 0  # never redeploy code that was not actually fixed


async def test_a_failed_redeploy_stops_the_cycle_rather_than_revalidating() -> None:
    from app.orchestrator.stages.validate import OUTCOME_ESCALATED, REASON_DEPLOY

    project = await project_ready_to_validate()
    live = FakeLiveRunner(CODE_FAILURE, PASSING)

    report = await LiveValidationController(
        live_runner=live,
        repair=FakeRepairLoop(FakeRepairResult("fixed")),
        deployer=FakeDeployer(status="degraded"),
        emitter=RecordingEmitter(),
    ).run(project)

    assert report.outcome == OUTCOME_ESCALATED
    assert report.reason == REASON_DEPLOY
    assert live.calls == 1  # re-validating a degraded deploy would only produce noise
    assert "degraded" in report.summary


async def test_a_provider_error_during_redeploy_is_reported_not_raised() -> None:
    from app.core.errors import ProviderError
    from app.orchestrator.stages.validate import OUTCOME_ESCALATED, REASON_DEPLOY

    project = await project_ready_to_validate()

    report = await LiveValidationController(
        live_runner=FakeLiveRunner(CODE_FAILURE),
        repair=FakeRepairLoop(FakeRepairResult("fixed")),
        deployer=FakeDeployer(error=ProviderError("render: quota exceeded")),
        emitter=RecordingEmitter(),
    ).run(project)

    assert report.outcome == OUTCOME_ESCALATED
    assert report.reason == REASON_DEPLOY
    assert "quota exceeded" in report.summary
