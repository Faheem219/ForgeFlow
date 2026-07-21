"""Isolation + cleanup (phase-44): specs must not contaminate each other, and must not leak.

Two distinct risks. **Contamination** would make the numbers meaningless — one spec's code or
results bleeding into another's score. **Leakage** would make a full corpus run progressively more
expensive: a sandbox container and an application database per spec, left running.
"""

from __future__ import annotations

import pytest
from beanie import PydanticObjectId

from app.db.models import Project, TestRun
from app.db.models.enums import Stage
from app.eval.metrics import OUTCOME_FAILED
from app.eval.runner import EvalRunner, Legs
from app.orchestrator.requirements import RequirementsService
from tests.eval.eval_fakes import (
    FakeConductor,
    FakeRepair,
    FakeTests,
    RecordingCleanup,
    StepClock,
    make_spec,
)

pytestmark = pytest.mark.usefixtures("mongo_db")


def _runner(cleanup: RecordingCleanup, **overrides: object) -> EvalRunner:
    defaults: dict[str, object] = {
        "conductor": FakeConductor(),
        "tests": FakeTests(),
        "repair": FakeRepair(),
        "cleanup": cleanup,
        "clock": StepClock(),
    }
    defaults.update(overrides)
    return EvalRunner(**defaults)  # type: ignore[arg-type]


# --------------------------------------------------------------------- isolation


async def test_each_spec_gets_its_own_project_and_database() -> None:
    report = await _runner(RecordingCleanup()).run_all(
        [make_spec(), make_spec(id="second"), make_spec(id="third")]
    )

    ids = [r.project_id for r in report.records]
    assert len(set(ids)) == 3

    projects = [await Project.get(PydanticObjectId(pid)) for pid in ids]
    db_names = {p.app_db_name for p in projects if p}
    assert len(db_names) == 3, "each spec must get its own application database"


async def test_one_specs_requirements_never_leak_into_another() -> None:
    first = make_spec()
    second = make_spec(
        id="second",
        title="Second",
        requirements={
            "features": [
                {"name": "Something else", "acceptance_criteria": [{"text": "Different."}]}
            ]
        },
        expected_features=["Something else"],
    )

    report = await _runner(RecordingCleanup()).run_all([first, second])

    requirements = RequirementsService()
    a = await requirements.latest(PydanticObjectId(report.records[0].project_id))
    b = await requirements.latest(PydanticObjectId(report.records[1].project_id))
    assert a is not None and b is not None
    assert [f.name for f in a.features] == ["Do the thing"]
    assert [f.name for f in b.features] == ["Something else"]


async def test_test_runs_are_scoped_to_their_own_project() -> None:
    report = await _runner(RecordingCleanup()).run_all([make_spec(), make_spec(id="second")])

    for record in report.records:
        pid = PydanticObjectId(record.project_id)
        runs = await TestRun.find({"project_id": pid}).to_list()
        assert runs, record.spec_id
        assert all(r.project_id == pid for r in runs)


async def test_a_spec_that_fails_does_not_derail_the_ones_after_it() -> None:
    conductor = FakeConductor(fail_on=Stage.build)
    good = EvalRunner(
        conductor=FakeConductor(),
        tests=FakeTests(),
        repair=FakeRepair(),
        cleanup=RecordingCleanup(),
        clock=StepClock(),
    )
    bad_then_good = EvalRunner(
        conductor=conductor,
        tests=FakeTests(),
        repair=FakeRepair(),
        cleanup=RecordingCleanup(),
        clock=StepClock(),
    )

    failed = await bad_then_good.run_all([make_spec(), make_spec(id="second")])
    healthy = await good.run_all([make_spec(id="third")])

    assert [r.outcome for r in failed.records] == [OUTCOME_FAILED, OUTCOME_FAILED]
    assert healthy.records[0].outcome != OUTCOME_FAILED


# --------------------------------------------------------------------- cleanup


async def test_every_spec_is_cleaned_up_after_its_run() -> None:
    cleanup = RecordingCleanup()

    report = await _runner(cleanup).run_all([make_spec(), make_spec(id="second")])

    assert cleanup.cleaned == [r.project_id for r in report.records]


async def test_cleanup_runs_even_when_the_pipeline_blows_up() -> None:
    """A leaked sandbox per failed spec is exactly how a corpus run becomes unrunnable."""
    cleanup = RecordingCleanup()

    metrics = await _runner(cleanup, conductor=FakeConductor(fail_on=Stage.build)).run_spec(
        make_spec()
    )

    assert metrics.outcome == OUTCOME_FAILED
    assert cleanup.cleaned == [metrics.project_id]


async def test_a_cleanup_that_fails_does_not_fail_the_run() -> None:
    """The measurement is the product; a stubborn container must not discard it."""
    cleanup = RecordingCleanup(explode=True)

    metrics = await _runner(cleanup).run_spec(make_spec())

    assert cleanup.cleaned == [metrics.project_id]
    assert metrics.outcome != OUTCOME_FAILED  # the run's own result survived
    assert metrics.first_pass.total > 0


async def test_cleanup_happens_for_the_deploy_legs_too() -> None:
    cleanup = RecordingCleanup()

    metrics = await _runner(cleanup).run_spec(make_spec(), legs=Legs.with_deploy())

    assert cleanup.cleaned == [metrics.project_id]


def test_the_costly_legs_are_opt_in_by_default() -> None:
    assert Legs().deploy is False and Legs().validate is False
    assert Legs.with_deploy().deploy and Legs.with_deploy().validate
    assert Legs().names() == ["build", "test", "repair"]
