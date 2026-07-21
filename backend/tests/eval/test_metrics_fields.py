"""Metrics (phase-44, D13): first-pass and post-repair are captured **separately**.

This is the phase's whole point. If the two numbers were ever taken from the same run, or a spec
that never ran tests scored 0% or 100%, the headline claim about the repair loop would be
unfalsifiable — and an unfalsifiable metric is not evidence.
"""

from __future__ import annotations

import pytest

from app.eval.metrics import (
    OUTCOME_DELIVERED,
    EvalReport,
    PassRate,
    SpecMetrics,
)
from app.eval.runner import EvalRunner
from tests.eval.eval_fakes import (
    FakeConductor,
    FakeRepair,
    FakeTests,
    RecordingCleanup,
    StepClock,
    make_spec,
)

pytestmark = pytest.mark.usefixtures("mongo_db")


def _runner(*, tests: FakeTests, repair: FakeRepair) -> EvalRunner:
    return EvalRunner(
        conductor=FakeConductor(),
        tests=tests,
        repair=repair,
        cleanup=RecordingCleanup(),
        clock=StepClock(),
    )


# --------------------------------------------------------------------- the two measurements


async def test_first_pass_and_post_repair_are_measured_separately() -> None:
    """3 of 5 before repair, 5 of 5 after — the contribution, observed rather than asserted."""
    runner = _runner(
        tests=FakeTests(passed=3, failed=2), repair=FakeRepair(passed=5, failed=0, iterations=2)
    )

    m = await runner.run_spec(make_spec())

    assert (m.first_pass.total, m.first_pass.passed) == (5, 3)
    assert (m.post_repair.total, m.post_repair.passed) == (5, 5)
    assert m.first_pass.rate == 0.6 and m.post_repair.rate == 1.0
    assert m.repair_delta == 0.4 and m.repaired is True


async def test_repair_iterations_and_regressions_are_recorded() -> None:
    runner = _runner(
        tests=FakeTests(passed=1, failed=3),
        repair=FakeRepair(passed=4, failed=0, iterations=3, regressions=1),
    )

    m = await runner.run_spec(make_spec())

    assert m.repair_iterations == 3
    assert m.regressions == 1
    assert m.repair_outcome == "fixed"


async def test_a_repair_that_does_not_help_shows_no_improvement() -> None:
    """The metric must be able to say "the loop did not work" — otherwise it proves nothing."""
    runner = _runner(
        tests=FakeTests(passed=2, failed=2),
        repair=FakeRepair(passed=2, failed=2, iterations=4, outcome="escalated"),
    )

    m = await runner.run_spec(make_spec())

    assert m.first_pass.rate == m.post_repair.rate == 0.5
    assert m.repair_delta == 0.0
    assert m.repaired is False
    assert m.repair_outcome == "escalated"
    assert m.outcome != OUTCOME_DELIVERED  # still failing → not delivered


async def test_a_green_first_pass_skips_repair_but_still_reports_both_numbers() -> None:
    repair = FakeRepair()
    runner = _runner(tests=FakeTests(passed=4, failed=0), repair=repair)

    m = await runner.run_spec(make_spec())

    assert repair.calls == []  # nothing to repair
    assert m.first_pass.rate == 1.0 and m.post_repair.rate == 1.0
    assert m.repair_iterations == 0
    assert m.repair_outcome == "not_needed"
    assert m.outcome == OUTCOME_DELIVERED


async def test_every_d13_field_is_present_on_a_record() -> None:
    m = await _runner(tests=FakeTests(), repair=FakeRepair()).run_spec(make_spec())
    data = m.to_dict()

    for key in (
        "first_pass",
        "post_repair",
        "repair_iterations",
        "regressions",
        "tokens",
        "inr_cost",
        "screenshot_to_url_seconds",
        "wall_clock_seconds",
        "outcome",
        "repair_delta",
    ):
        assert key in data, key


# --------------------------------------------------------------------- honesty of the numbers


def test_an_empty_run_is_unknown_not_a_flattering_zero_or_perfect_score() -> None:
    empty = PassRate()
    assert empty.rate is None
    assert empty.green is False  # a run with no tests never counts as passing


def test_a_spec_that_never_ran_tests_has_no_pass_rate_and_no_delta() -> None:
    m = SpecMetrics(spec_id="never-ran")
    assert m.first_pass.rate is None
    assert m.repair_delta is None
    assert m.repaired is False


def test_crashed_specs_are_excluded_from_the_means_not_counted_as_zero() -> None:
    """Otherwise one broken spec would silently drag the headline number down."""
    scored = SpecMetrics(
        spec_id="ok",
        first_pass=PassRate(total=4, passed=2, failed=2),
        post_repair=PassRate(total=4, passed=4),
        outcome=OUTCOME_DELIVERED,
    )
    crashed = SpecMetrics(spec_id="broken", error="boom")
    report = EvalReport(records=[scored, crashed])

    agg = report.aggregate()
    assert agg["specs"] == 2 and agg["scored"] == 1
    assert agg["mean_first_pass_rate"] == 0.5  # not 0.25
    assert agg["mean_post_repair_rate"] == 1.0
    assert agg["failed"] == 1


def test_the_aggregate_counts_what_repair_actually_improved() -> None:
    improved = SpecMetrics(
        spec_id="a",
        first_pass=PassRate(total=4, passed=1, failed=3),
        post_repair=PassRate(total=4, passed=4),
        repair_iterations=2,
    )
    unchanged = SpecMetrics(
        spec_id="b",
        first_pass=PassRate(total=2, passed=1, failed=1),
        post_repair=PassRate(total=2, passed=1, failed=1),
        repair_iterations=3,
    )

    agg = EvalReport(records=[improved, unchanged]).aggregate()

    assert agg["specs_improved_by_repair"] == 1
    assert agg["green_first_pass"] == 0
    assert agg["green_post_repair"] == 1
    assert agg["total_repair_iterations"] == 5
    assert agg["mean_repair_delta"] == 0.375  # (0.75 + 0.0) / 2


def test_an_empty_report_reports_nothing_rather_than_zero() -> None:
    agg = EvalReport().aggregate()
    assert agg["specs"] == 0
    assert agg["mean_first_pass_rate"] is None
    assert agg["mean_post_repair_rate"] is None
