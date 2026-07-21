"""Evaluation reporting (phase-45, D13) — turning measurements into evidence.

Phase-44 produces one JSON report per run. This layer reads those files, aggregates across them,
and exports a **stable** CSV/markdown schema so results survive UI changes and can be analysed
elsewhere (a spreadsheet, a paper's appendix).

Two commitments make the output trustworthy:

**The schema is frozen.** ``CSV_COLUMNS`` is the contract — appended to, never reordered or
renamed — because an export whose columns move is worthless for comparing two runs a month apart.

**Missing is not zero.** A spec that never reached the test stage exports an empty cell, not a
``0``. Medians and means are taken over the specs that actually produced a number, so one crashed
spec cannot quietly drag the headline down. Everything here reads; nothing mutates a run.
"""

from __future__ import annotations

import csv
import io
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.eval.runner import RESULTS_DIR

logger = logging.getLogger(__name__)

#: The export contract. **Append only** — never reorder or rename.
CSV_COLUMNS: tuple[str, ...] = (
    "spec_id",
    "title",
    "difficulty",
    "outcome",
    "first_pass_total",
    "first_pass_passed",
    "first_pass_rate",
    "post_repair_total",
    "post_repair_passed",
    "post_repair_rate",
    "repair_delta",
    "repair_iterations",
    "repair_outcome",
    "regressions",
    "tokens",
    "inr_cost",
    "screenshot_to_url_seconds",
    "wall_clock_seconds",
    "live_url",
    "error",
)


@dataclass
class Distribution:
    """A small numeric summary. Empty input yields all-``None`` rather than zeros."""

    count: int = 0
    mean: float | None = None
    median: float | None = None
    min: float | None = None
    max: float | None = None
    values: list[float] = field(default_factory=list)

    @classmethod
    def of(cls, values: list[float]) -> Distribution:
        ordered = sorted(values)
        if not ordered:
            return cls()
        return cls(
            count=len(ordered),
            mean=round(sum(ordered) / len(ordered), 4),
            median=round(_median(ordered), 4),
            min=round(ordered[0], 4),
            max=round(ordered[-1], 4),
            values=[round(v, 4) for v in ordered],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "mean": self.mean,
            "median": self.median,
            "min": self.min,
            "max": self.max,
            "values": self.values,
        }


def _median(ordered: list[float]) -> float:
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def _rate(record: dict[str, Any], key: str) -> float | None:
    block = record.get(key) or {}
    value = block.get("rate")
    return float(value) if isinstance(value, (int, float)) else None


def load_reports(results_dir: Path | None = None) -> list[dict[str, Any]]:
    """Every run report on disk, newest first. A corrupt file is skipped, loudly."""
    directory = results_dir or RESULTS_DIR
    if not directory.is_dir():
        return []

    reports: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json"), reverse=True):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            logger.warning("eval: skipping unreadable report %s", path, exc_info=True)
            continue
        if isinstance(payload, dict) and isinstance(payload.get("records"), list):
            payload["source"] = path.name
            reports.append(payload)
        else:
            logger.warning("eval: %s is not a run report", path)
    return reports


def latest_records(results_dir: Path | None = None) -> list[dict[str, Any]]:
    """The most recent run's spec records — what the dashboard charts by default."""
    reports = load_reports(results_dir)
    return list(reports[0]["records"]) if reports else []


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate a run's records into the numbers a dashboard or a paper quotes.

    Specs that produced no pass rate are counted in ``specs`` but excluded from every
    distribution — reporting them as 0% would understate the system, reporting them as 100% would
    flatter it, and both would be false.
    """
    scored = [r for r in records if _rate(r, "first_pass") is not None]

    first = [_rate(r, "first_pass") for r in scored]
    post = [_rate(r, "post_repair") for r in scored]
    deltas = [p - f for f, p in zip(first, post, strict=True) if f is not None and p is not None]

    by_difficulty: dict[str, dict[str, Any]] = {}
    for record in scored:
        grade = str(record.get("difficulty") or "unknown")
        bucket = by_difficulty.setdefault(grade, {"specs": 0, "first": [], "post": []})
        bucket["specs"] += 1
        bucket["first"].append(_rate(record, "first_pass"))
        bucket["post"].append(_rate(record, "post_repair"))

    return {
        "specs": len(records),
        "scored": len(scored),
        "outcomes": {
            outcome: sum(1 for r in records if r.get("outcome") == outcome)
            for outcome in ("delivered", "escalated", "failed")
        },
        "first_pass": Distribution.of([f for f in first if f is not None]).to_dict(),
        "post_repair": Distribution.of([p for p in post if p is not None]).to_dict(),
        "repair_delta": Distribution.of(deltas).to_dict(),
        "repair_iterations": Distribution.of(
            [float(r.get("repair_iterations") or 0) for r in records]
        ).to_dict(),
        "tokens": Distribution.of([float(r.get("tokens") or 0) for r in records]).to_dict(),
        "inr_cost": Distribution.of([float(r.get("inr_cost") or 0) for r in records]).to_dict(),
        # Only specs that actually reached a live URL have a time-to-URL.
        "screenshot_to_url_seconds": Distribution.of(
            [
                float(r["screenshot_to_url_seconds"])
                for r in records
                if r.get("screenshot_to_url_seconds") is not None
            ]
        ).to_dict(),
        "wall_clock_seconds": Distribution.of(
            [float(r.get("wall_clock_seconds") or 0) for r in records]
        ).to_dict(),
        "specs_improved_by_repair": sum(1 for d in deltas if d > 0),
        "specs_unchanged_by_repair": sum(1 for d in deltas if d == 0),
        "green_first_pass": sum(1 for r in scored if _green(r, "first_pass")),
        "green_post_repair": sum(1 for r in scored if _green(r, "post_repair")),
        "total_regressions": sum(int(r.get("regressions") or 0) for r in records),
        "by_difficulty": {
            grade: {
                "specs": bucket["specs"],
                "first_pass_mean": Distribution.of(
                    [v for v in bucket["first"] if v is not None]
                ).mean,
                "post_repair_mean": Distribution.of(
                    [v for v in bucket["post"] if v is not None]
                ).mean,
            }
            for grade, bucket in sorted(by_difficulty.items())
        },
    }


def _green(record: dict[str, Any], key: str) -> bool:
    block = record.get(key) or {}
    return bool(block.get("total")) and not block.get("failed")


# --------------------------------------------------------------------- exports


def _row(record: dict[str, Any]) -> dict[str, Any]:
    first = record.get("first_pass") or {}
    post = record.get("post_repair") or {}
    return {
        "spec_id": record.get("spec_id", ""),
        "title": record.get("title", ""),
        "difficulty": record.get("difficulty", ""),
        "outcome": record.get("outcome", ""),
        "first_pass_total": first.get("total"),
        "first_pass_passed": first.get("passed"),
        "first_pass_rate": first.get("rate"),
        "post_repair_total": post.get("total"),
        "post_repair_passed": post.get("passed"),
        "post_repair_rate": post.get("rate"),
        "repair_delta": record.get("repair_delta"),
        "repair_iterations": record.get("repair_iterations"),
        "repair_outcome": record.get("repair_outcome"),
        "regressions": record.get("regressions"),
        "tokens": record.get("tokens"),
        "inr_cost": record.get("inr_cost"),
        "screenshot_to_url_seconds": record.get("screenshot_to_url_seconds"),
        "wall_clock_seconds": record.get("wall_clock_seconds"),
        "live_url": record.get("live_url"),
        "error": record.get("error"),
    }


def to_csv(records: list[dict[str, Any]]) -> str:
    """The stable export. ``None`` becomes an empty cell — never a fabricated ``0``."""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(CSV_COLUMNS), extrasaction="ignore")
    writer.writeheader()
    for record in records:
        writer.writerow({k: ("" if v is None else v) for k, v in _row(record).items()})
    return buffer.getvalue()


def to_markdown(records: list[dict[str, Any]], summary: dict[str, Any] | None = None) -> str:
    """A report suited to a paper appendix or a pitch deck: headline, then the per-spec table."""
    summary = summary if summary is not None else summarize(records)
    first, post = summary["first_pass"], summary["repair_delta"]
    post_rate = summary["post_repair"]

    lines = [
        "# ForgeFlow evaluation",
        "",
        f"**{summary['specs']} specs** · {summary['outcomes']['delivered']} delivered · "
        f"{summary['outcomes']['escalated']} escalated · {summary['outcomes']['failed']} failed",
        "",
        "## Headline",
        "",
        "| Metric | Mean | Median |",
        "|---|---|---|",
        f"| First-pass pass rate | {_pct(first['mean'])} | {_pct(first['median'])} |",
        f"| Post-repair pass rate | {_pct(post_rate['mean'])} | {_pct(post_rate['median'])} |",
        f"| Repair delta | {_pct(post['mean'], signed=True)} | "
        f"{_pct(post['median'], signed=True)} |",
        f"| Repair iterations | {_num(summary['repair_iterations']['mean'])} | "
        f"{_num(summary['repair_iterations']['median'])} |",
        f"| Tokens per spec | {_num(summary['tokens']['mean'])} | "
        f"{_num(summary['tokens']['median'])} |",
        f"| Cost per spec (₹) | {_num(summary['inr_cost']['mean'])} | "
        f"{_num(summary['inr_cost']['median'])} |",
        f"| Screenshot → URL (s) | {_num(summary['screenshot_to_url_seconds']['mean'])} | "
        f"{_num(summary['screenshot_to_url_seconds']['median'])} |",
        "",
        f"Repair improved **{summary['specs_improved_by_repair']} of {summary['scored']}** scored "
        f"specs; {summary['green_first_pass']} were already green first-pass and "
        f"{summary['green_post_repair']} were green after repair.",
        "",
    ]

    if summary["by_difficulty"]:
        lines += [
            "## By difficulty",
            "",
            "| Difficulty | Specs | First-pass | Post-repair |",
            "|---|---|---|---|",
        ]
        lines += [
            f"| {grade} | {b['specs']} | {_pct(b['first_pass_mean'])} | "
            f"{_pct(b['post_repair_mean'])} |"
            for grade, b in summary["by_difficulty"].items()
        ]
        lines.append("")

    lines += [
        "## Per spec",
        "",
        "| Spec | Difficulty | Outcome | First-pass | Post-repair | Δ | Iterations | Tokens |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for record in records:
        row = _row(record)
        lines.append(
            f"| {row['spec_id']} | {row['difficulty']} | {row['outcome']} | "
            f"{_pct(row['first_pass_rate'])} | {_pct(row['post_repair_rate'])} | "
            f"{_pct(row['repair_delta'], signed=True)} | {row['repair_iterations']} | "
            f"{row['tokens']} |"
        )
    lines.append("")
    return "\n".join(lines)


def _pct(value: float | None, *, signed: bool = False) -> str:
    if value is None:
        return "—"  # never a fabricated 0%
    return f"{value * 100:+.0f}%" if signed else f"{value * 100:.0f}%"


def _num(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.0f}" if float(value).is_integer() else f"{value:.2f}"


__all__ = [
    "CSV_COLUMNS",
    "Distribution",
    "latest_records",
    "load_reports",
    "summarize",
    "to_csv",
    "to_markdown",
]
