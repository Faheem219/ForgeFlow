"""Aggregation (phase-45): the dashboard's numbers must match the phase-44 records exactly.

The risk this guards against is a reporting layer that quietly improves the story — averaging over
specs that never ran, treating a missing measurement as zero, or counting a repair that changed
nothing as an improvement. Each of those would make the evidence wrong in the flattering direction.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.eval.report import Distribution, latest_records, load_reports, summarize


def record(
    spec_id: str,
    *,
    first: tuple[int, int] | None = (4, 2),
    post: tuple[int, int] | None = (4, 4),
    difficulty: str = "simple",
    outcome: str = "delivered",
    iterations: int = 2,
    tokens: int = 1000,
    inr: float = 2.0,
    ttu: float | None = None,
    regressions: int = 0,
) -> dict[str, Any]:
    def block(pair: tuple[int, int] | None) -> dict[str, Any]:
        if pair is None:
            return {"total": 0, "passed": 0, "failed": 0, "rate": None}
        total, passed = pair
        return {
            "total": total,
            "passed": passed,
            "failed": total - passed,
            "rate": round(passed / total, 4),
        }

    f, p = block(first), block(post)
    delta = None if f["rate"] is None or p["rate"] is None else round(p["rate"] - f["rate"], 4)
    return {
        "spec_id": spec_id,
        "title": spec_id.title(),
        "difficulty": difficulty,
        "outcome": outcome,
        "first_pass": f,
        "post_repair": p,
        "repair_delta": delta,
        "repair_iterations": iterations,
        "repair_outcome": "fixed",
        "regressions": regressions,
        "tokens": tokens,
        "inr_cost": inr,
        "screenshot_to_url_seconds": ttu,
        "wall_clock_seconds": 12.5,
        "live_url": None,
        "error": None,
    }


# --------------------------------------------------------------------- distributions


def test_a_distribution_reports_mean_median_and_range() -> None:
    d = Distribution.of([1.0, 2.0, 3.0, 10.0])
    assert d.count == 4 and d.mean == 4.0 and d.median == 2.5
    assert d.min == 1.0 and d.max == 10.0


def test_an_empty_distribution_is_all_none_not_zero() -> None:
    """Zero would be a claim; None is the truth when nothing was measured."""
    d = Distribution.of([])
    assert d.count == 0
    assert d.mean is None and d.median is None and d.min is None and d.max is None


def test_the_median_of_an_odd_sample_is_the_middle_value() -> None:
    assert Distribution.of([5.0, 1.0, 3.0]).median == 3.0


# --------------------------------------------------------------------- summary


def test_both_pass_rates_are_aggregated_separately() -> None:
    summary = summarize([record("a", first=(4, 1), post=(4, 4)), record("b", first=(4, 3))])

    assert summary["first_pass"]["mean"] == 0.5  # (0.25 + 0.75) / 2
    assert summary["post_repair"]["mean"] == 1.0
    assert summary["repair_delta"]["mean"] == 0.5


def test_specs_that_never_ran_are_excluded_from_the_means() -> None:
    """A crashed spec must not be averaged in as 0% — that would understate the system."""
    summary = summarize(
        [record("ok", first=(4, 2), post=(4, 4)), record("broken", first=None, post=None)]
    )

    assert summary["specs"] == 2 and summary["scored"] == 1
    assert summary["first_pass"]["mean"] == 0.5  # not 0.25
    assert summary["first_pass"]["count"] == 1


def test_outcomes_are_counted_by_kind() -> None:
    summary = summarize(
        [
            record("a", outcome="delivered"),
            record("b", outcome="escalated"),
            record("c", outcome="failed", first=None, post=None),
        ]
    )

    assert summary["outcomes"] == {"delivered": 1, "escalated": 1, "failed": 1}


def test_only_real_improvements_count_as_improved() -> None:
    summary = summarize(
        [
            record("improved", first=(4, 1), post=(4, 4)),
            record("unchanged", first=(4, 2), post=(4, 2)),
        ]
    )

    assert summary["specs_improved_by_repair"] == 1
    assert summary["specs_unchanged_by_repair"] == 1


def test_green_counts_distinguish_before_and_after_repair() -> None:
    summary = summarize(
        [record("a", first=(4, 4), post=(4, 4)), record("b", first=(4, 2), post=(4, 4))]
    )

    assert summary["green_first_pass"] == 1
    assert summary["green_post_repair"] == 2


def test_time_to_url_only_counts_specs_that_reached_one() -> None:
    summary = summarize([record("a", ttu=90.0), record("b", ttu=None)])

    assert summary["screenshot_to_url_seconds"]["count"] == 1
    assert summary["screenshot_to_url_seconds"]["mean"] == 90.0


def test_cost_and_iterations_are_summarised_across_every_spec() -> None:
    summary = summarize(
        [
            record("a", tokens=1000, inr=2.0, iterations=1),
            record("b", tokens=3000, inr=4.0, iterations=5),
        ]
    )

    assert summary["tokens"]["mean"] == 2000 and summary["tokens"]["max"] == 3000
    assert summary["inr_cost"]["mean"] == 3.0
    assert summary["repair_iterations"]["median"] == 3.0


def test_results_are_broken_down_by_difficulty() -> None:
    """The gradient is the point of the corpus — the report must show it."""
    summary = summarize(
        [
            record("easy", difficulty="simple", first=(4, 4), post=(4, 4)),
            record("hard", difficulty="complex", first=(4, 1), post=(4, 3)),
        ]
    )

    assert summary["by_difficulty"]["simple"]["first_pass_mean"] == 1.0
    assert summary["by_difficulty"]["complex"]["first_pass_mean"] == 0.25
    assert summary["by_difficulty"]["complex"]["post_repair_mean"] == 0.75


def test_an_empty_run_summarises_to_nothing_rather_than_zeros() -> None:
    summary = summarize([])
    assert summary["specs"] == 0
    assert summary["first_pass"]["mean"] is None
    assert summary["post_repair"]["mean"] is None


def test_regressions_are_totalled() -> None:
    assert (
        summarize([record("a", regressions=1), record("b", regressions=2)])["total_regressions"]
        == 3
    )


# --------------------------------------------------------------------- loading reports


def _write_report(directory: Path, name: str, records: list[dict[str, Any]], **extra: Any) -> Path:
    path = directory / name
    path.write_text(
        json.dumps({"started_at": "2026-07-21T10:00:00", "records": records, **extra}),
        encoding="utf-8",
    )
    return path


def test_reports_are_returned_newest_first(tmp_path: Path) -> None:
    _write_report(tmp_path, "eval-100.json", [record("a")])
    _write_report(tmp_path, "eval-200.json", [record("b")])

    reports = load_reports(tmp_path)

    assert [r["source"] for r in reports] == ["eval-200.json", "eval-100.json"]
    assert latest_records(tmp_path)[0]["spec_id"] == "b"


def test_a_corrupt_report_is_skipped_not_fatal(tmp_path: Path) -> None:
    """One unreadable file must not hide every other run's evidence."""
    (tmp_path / "eval-bad.json").write_text("{not json", encoding="utf-8")
    _write_report(tmp_path, "eval-good.json", [record("a")])

    reports = load_reports(tmp_path)

    assert [r["source"] for r in reports] == ["eval-good.json"]


def test_a_json_file_that_is_not_a_run_report_is_ignored(tmp_path: Path) -> None:
    (tmp_path / "eval-other.json").write_text(json.dumps({"hello": "world"}), encoding="utf-8")

    assert load_reports(tmp_path) == []


def test_a_missing_results_directory_is_simply_empty(tmp_path: Path) -> None:
    assert load_reports(tmp_path / "nowhere") == []
    assert latest_records(tmp_path / "nowhere") == []
