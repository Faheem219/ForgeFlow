"""The build takes the path the scope decision chose (phase-60).

The saving is in *planning*, never in *proof*: a small change skips BuildPlanner and the
``phase-plan/`` rewrite, and still runs the same phase-55 verification and bounded repair.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.build_plan import BuildPhase, BuildPlan, incremental_plan
from app.agents.build_scope import BuildScope, ScopeDecision


def test_a_small_change_becomes_exactly_one_phase() -> None:
    plan = incremental_plan("add a sign-in button to the home page")

    assert len(plan.phases) == 1
    assert plan.phases[0].goal == "add a sign-in button to the home page"


def test_the_synthetic_phase_typechecks_both_packages() -> None:
    """`wiring` is the one kind whose gate spans FE and BE — a small change is not known in
    advance to stay on one side of the app."""
    phase = incremental_plan("fix it").phases[0]

    assert phase.kind == "wiring"
    assert phase.package is None


def test_the_synthetic_phase_survives_validation() -> None:
    """It goes through the same validate() gate a model-produced plan does."""
    plan = incremental_plan("fix it").validate(max_phases=8)

    assert len(plan.phases) == 1


def test_a_small_plan_has_no_assumptions_to_report() -> None:
    assert incremental_plan("fix it").assumptions == []


# --------------------------------------------------------------------- the decision object


def test_is_small_reflects_the_scope() -> None:
    assert ScopeDecision(BuildScope.small, "x").is_small is True
    assert ScopeDecision(BuildScope.large, "x").is_small is False


def test_a_decision_defaults_to_unclassified() -> None:
    """Only a real model verdict sets classified, so the UI can be honest about fallbacks."""
    assert ScopeDecision(BuildScope.large, "x").classified is False


# --------------------------------------------------------------------- resume safety


def test_two_small_changes_must_not_share_a_resume_id() -> None:
    """The synthetic phase id is stable, so resuming across refines would skip the second request.

    codegen forces resume_from=0 on the small path; this pins the property that makes that
    necessary, so the reason survives if anyone removes the guard.
    """
    first = incremental_plan("fix the button")
    second = incremental_plan("fix the header")

    assert first.phases[0].id == second.phases[0].id


# --------------------------------------------------------------------- report shape


def test_the_report_records_the_scope_and_why() -> None:
    from app.agents.codegen import BuildReport

    report = BuildReport(
        boot_status="ok",
        files_changed=[],
        features_built=[],
        follow_ups=[],
        notes=[],
        commit=None,
        tests_passed=None,
        summary="",
        plan="",
        scope="small",
        scope_reason="only the home page component changes",
    )

    assert report.to_dict()["scope"] == "small"
    assert report.to_dict()["scope_reason"] == "only the home page component changes"


def test_an_older_report_without_scope_still_parses() -> None:
    """Reports written before phase-60 must keep rendering — the fields are defaulted."""
    from app.agents.codegen import BuildReport

    report = BuildReport(
        boot_status="ok",
        files_changed=[],
        features_built=[],
        follow_ups=[],
        notes=[],
        commit=None,
        tests_passed=None,
        summary="",
        plan="",
    )

    assert report.to_dict()["scope"] == ""


def test_an_unphased_report_is_still_vacuously_complete() -> None:
    """phases_complete drives stage completion; a small pass records one phase, not zero."""
    from app.agents.codegen import BuildReport

    report = BuildReport(
        boot_status="ok",
        files_changed=[],
        features_built=[],
        follow_ups=[],
        notes=[],
        commit=None,
        tests_passed=None,
        summary="",
        plan="",
    )

    assert report.phases_complete is True


# --------------------------------------------------------------------- the override


@pytest.mark.parametrize(
    ("payload", "expected"),
    [("small", BuildScope.small), ("large", BuildScope.large), ("LARGE", BuildScope.large)],
)
def test_a_valid_override_is_read_off_the_payload(payload: Any, expected: BuildScope) -> None:
    from app.orchestrator.stages.build import _forced_scope

    assert _forced_scope(payload) is expected


@pytest.mark.parametrize("payload", [None, "", "medium", 3, {"scope": "small"}])
def test_an_unrecognised_override_is_ignored_not_fatal(payload: Any) -> None:
    from app.orchestrator.stages.build import _forced_scope

    assert _forced_scope(payload) is None


def test_a_plan_with_many_phases_is_unaffected_by_the_small_path() -> None:
    """Regression guard for phase-56: the large path's shape is untouched."""
    plan = BuildPlan(
        phases=[
            BuildPhase(id="be-core", title="Backend", kind="backend", goal="g"),
            BuildPhase(id="fe-core", title="Frontend", kind="frontend", goal="g"),
        ]
    )

    assert len(plan.validate(max_phases=8).phases) == 2
