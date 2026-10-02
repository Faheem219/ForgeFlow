"""Extending a plan with a change request (phase-67) — pure, no I/O."""

from __future__ import annotations

from app.agents.build_phases import PhaseOutcome
from app.agents.build_plan import (
    BuildPhase,
    BuildPlan,
    PlanChange,
    as_phase_kind,
    change_headline,
    extend_plan,
    incremental_plan,
    render_markdown,
)
from app.agents.codegen import BuildReport


def _plan(*ids: str) -> BuildPlan:
    return BuildPlan(
        phases=[BuildPhase(id=i, title=i.upper(), kind="backend", goal=f"do {i}") for i in ids]
    )


def test_the_change_is_appended_after_every_earlier_phase() -> None:
    merged = extend_plan(_plan("a", "b"), _plan("c"), instruction="add c")

    assert [p.id for p in merged.phases] == ["a", "b", "c"]
    assert merged.changes == [PlanChange(instruction="add c", phase_ids=["c"])]


def test_the_merged_plan_is_not_reordered() -> None:
    """A new backend phase must not be sorted in front of an old frontend one: the build runs the
    change by slicing past the history."""
    prior = BuildPlan(phases=[BuildPhase(id="fe", title="FE", kind="frontend", goal="")])
    change = BuildPlan(phases=[BuildPhase(id="be", title="BE", kind="backend", goal="")])

    merged = extend_plan(prior, change, instruction="x")

    assert [p.id for p in merged.phases] == ["fe", "be"]


def test_colliding_ids_are_renamed_and_their_dependencies_follow() -> None:
    change = BuildPlan(
        phases=[
            BuildPhase(id="a", title="A2", kind="backend", goal=""),
            BuildPhase(id="d", title="D", kind="frontend", goal="", depends_on=["a"]),
        ]
    )

    merged = extend_plan(_plan("a", "b"), change, instruction="x")

    assert [p.id for p in merged.phases] == ["a", "b", "a-2", "d"]
    assert merged.phases[3].depends_on == ["a-2"]
    assert merged.changes[-1].phase_ids == ["a-2", "d"]


def test_changes_accumulate_oldest_first() -> None:
    once = extend_plan(_plan("a"), incremental_plan("first"), instruction="first")
    twice = extend_plan(once, incremental_plan("second"), instruction="second")

    assert [p.id for p in twice.phases] == ["a", "change", "change-2"]
    assert [c.instruction for c in twice.changes] == ["first", "second"]


def test_a_persisted_plan_reads_back_exactly() -> None:
    merged = extend_plan(_plan("b", "a"), _plan("c"), instruction="add c")

    restored = BuildPlan.from_dict(merged.to_dict())

    assert [p.id for p in restored.phases] == ["b", "a", "c"]  # order kept, not re-sorted
    assert restored.changes == merged.changes
    assert restored.phases[0].goal == "do b"


def test_reading_back_tolerates_junk() -> None:
    plan = BuildPlan.from_dict({"phases": [{"id": "x"}, "nope", {"id": "y", "title": "Y"}]})

    assert [p.id for p in plan.phases] == ["y"]
    assert plan.changes == []


def test_the_plan_file_lists_each_change_under_its_own_heading() -> None:
    merged = extend_plan(_plan("a"), _plan("c"), instruction="add tagging to todos")

    text = render_markdown(merged, {"a": "done", "c": "running"})

    assert text.index("**1. A**") < text.index("## Change 1: add tagging to todos")
    assert text.index("## Change 1:") < text.index("**2. C**")
    assert "- [x] **1. A**" in text
    assert "- [>] **2. C**" in text
    assert "> add tagging to todos" in text


def test_a_plan_without_changes_renders_as_before() -> None:
    text = render_markdown(_plan("a", "b"))

    assert "## Change" not in text
    assert "**1. A**" in text and "**2. B**" in text


def test_a_change_title_is_its_request_shortened() -> None:
    assert incremental_plan("fix the button").phases[0].title == "Change: fix the button"
    long = "make " + "every single page in the whole application " * 3
    headline = change_headline(long)
    assert len(headline) <= 61 and headline.endswith("…")
    assert change_headline("first line\nsecond line") == "first line"


def test_unknown_kinds_fall_back() -> None:
    assert as_phase_kind("FRONTEND") == "frontend"
    assert as_phase_kind("nonsense") == "backend"
    assert as_phase_kind(None, default="wiring") == "wiring"


def _report(statuses: list[str], prior: int) -> BuildReport:
    return BuildReport(
        boot_status="healthy",
        files_changed=[],
        features_built=[],
        follow_ups=[],
        notes=[],
        commit=None,
        tests_passed=None,
        summary="",
        plan="",
        phases=[
            PhaseOutcome(id=f"p{i}", title=f"P{i}", kind="backend", status=s)
            for i, s in enumerate(statuses)
        ],
        prior_phases=prior,
    )


def test_completion_is_judged_on_the_phases_this_build_ran() -> None:
    report = _report(["done", "failed", "done"], prior=2)

    assert [p.id for p in report.run_phases] == ["p2"]
    assert report.phases_complete


def test_with_no_history_every_phase_counts_as_before() -> None:
    assert not _report(["done", "failed"], prior=0).phases_complete
    assert _report([], prior=0).phases_complete
