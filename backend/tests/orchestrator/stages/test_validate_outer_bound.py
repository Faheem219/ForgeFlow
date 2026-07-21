"""The outer bound (phase-40, D7 one level up).

The inner repair loop is already capped (phase-31). Without a second cap, a repair that keeps
"succeeding" against a deployment that keeps failing — a broken deploy config, say — would redeploy
forever. This is the guard, plus the env/code split that stops the loop burning repair iterations on
problems no code patch can fix.
"""

from __future__ import annotations

import pytest

from app.core.config import reset_config
from app.orchestrator.stages.validate import (
    DIAGNOSIS_CODE,
    DIAGNOSIS_ENV,
    OUTCOME_ESCALATED,
    REASON_CAP,
    REASON_ENV,
    LiveValidationController,
    diagnose,
)
from tests.orchestrator.stages.validate_fakes import (
    CODE_FAILURE,
    ENV_FAILURE,
    FakeDeployer,
    FakeLiveRunner,
    FakeRepairLoop,
    FakeRepairResult,
    RecordingEmitter,
    project_ready_to_validate,
)

pytestmark = pytest.mark.usefixtures("mongo_db")


async def test_a_site_that_never_goes_green_stops_at_the_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VALIDATE_MAX_CYCLES", "3")
    reset_config()
    project = await project_ready_to_validate()
    live = FakeLiveRunner(CODE_FAILURE)  # always fails
    repair = FakeRepairLoop(FakeRepairResult("fixed"))  # always "fixes"
    deployer = FakeDeployer()  # always deploys

    report = await LiveValidationController(
        live_runner=live, repair=repair, deployer=deployer, emitter=RecordingEmitter()
    ).run(project)

    assert report.outcome == OUTCOME_ESCALATED
    assert report.reason == REASON_CAP
    # Bounded exactly: 3 validations, and no redeploy after the last one.
    assert live.calls == 3
    assert len(report.cycles) == 3
    assert deployer.calls == 3
    assert "3 repair→redeploy→re-validate cycles" in report.summary


async def test_the_cap_is_configurable_and_always_at_least_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VALIDATE_MAX_CYCLES", "1")
    reset_config()
    project = await project_ready_to_validate()
    live = FakeLiveRunner(CODE_FAILURE)

    report = await LiveValidationController(
        live_runner=live,
        repair=FakeRepairLoop(FakeRepairResult("fixed")),
        deployer=FakeDeployer(),
        emitter=RecordingEmitter(),
    ).run(project)

    assert live.calls == 1
    assert report.reason == REASON_CAP
    assert "1 repair→redeploy→re-validate cycle" in report.summary  # singular


async def test_an_env_failure_escalates_instead_of_burning_repair_cycles() -> None:
    """A code patch cannot fix a site that refuses connections — say so and stop."""
    project = await project_ready_to_validate()
    repair = FakeRepairLoop(FakeRepairResult("fixed"))
    deployer = FakeDeployer()

    report = await LiveValidationController(
        live_runner=FakeLiveRunner(ENV_FAILURE),
        repair=repair,
        deployer=deployer,
        emitter=RecordingEmitter(),
    ).run(project)

    assert report.outcome == OUTCOME_ESCALATED
    assert report.reason == REASON_ENV
    assert report.cycles[0].diagnosis == DIAGNOSIS_ENV
    assert repair.calls == [] and deployer.calls == 0  # not one wasted iteration
    assert "env wiring" in report.summary


async def test_the_escalation_names_the_failing_live_tests() -> None:
    project = await project_ready_to_validate()

    report = await LiveValidationController(
        live_runner=FakeLiveRunner(CODE_FAILURE),
        repair=FakeRepairLoop(FakeRepairResult("fixed")),
        deployer=FakeDeployer(),
        emitter=RecordingEmitter(),
    ).run(project)

    names = [f["name"] for f in report.failing_tests]
    assert "[ac-add] adds a todo" in names


# --------------------------------------------------------------------- diagnosis (pure)


def test_connectivity_errors_are_env_not_code() -> None:
    diagnosis, why = diagnose(ENV_FAILURE, "live", "")
    assert diagnosis == DIAGNOSIS_ENV
    assert "connectivity" in why


def test_assertion_failures_are_code() -> None:
    diagnosis, why = diagnose(CODE_FAILURE, "live", "")
    assert diagnosis == DIAGNOSIS_CODE
    assert "application behaviour" in why


def test_a_degraded_deployment_is_env_whatever_the_tests_say() -> None:
    """If the deploy itself is not live, the tests are describing a symptom, not the cause."""
    diagnosis, why = diagnose(CODE_FAILURE, "degraded", "")
    assert diagnosis == DIAGNOSIS_ENV
    assert "degraded" in why


def test_a_5xx_from_the_backend_is_env() -> None:
    failures = [
        {
            "name": "[ac-add] adds a todo",
            "status": "failed",
            "failure": {"message": "expected 200, received 503"},
        }
    ]
    assert diagnose(failures, "live", "")[0] == DIAGNOSIS_ENV


def test_a_crashed_suite_with_deploy_errors_is_env() -> None:
    diagnosis, why = diagnose([], "live", "be: FAILED — Error: missing MONGODB_URI")
    assert diagnosis == DIAGNOSIS_ENV
    assert "deploy log" in why
