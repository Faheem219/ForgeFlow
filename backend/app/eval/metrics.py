"""Evaluation metrics (phase-44, D13) — the numbers that make the contribution arguable.

The headline claim of the project is that a **bounded, diff-aware repair loop** turns builds that
almost work into builds that do. That claim is only meaningful if two things are measured
*separately*:

- ``first_pass`` — what the generated code scored **before any repair**, and
- ``post_repair`` — what it scored after the loop ran.

Each is taken twice. The *test-level* rate counts every test in the workspace, the skeleton's own
included. The *criterion-level* rate is the one that answers "does the app do what the spec asked":
a spec acceptance criterion counts as met only when at least one test tagged with its id ran and
none of them failed. A criterion with no test is unmet — never dropped from the denominator.

Everything else here exists to keep those two honest: iteration and regression counts say how hard
the loop worked, tokens/₹ say what it cost, and the wall clock says whether the whole thing is fast
enough to demo. A metric that cannot fail is not evidence, so a spec that never reached the test
stage records ``None`` pass rates rather than a flattering zero-of-zero.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from typing import Any

from app.db.models.common import utcnow

# Terminal outcomes for one spec's run.
OUTCOME_DELIVERED = "delivered"  # green tests (and a verified deployment when that leg ran)
OUTCOME_ESCALATED = "escalated"  # the pipeline stopped and asked for a human
OUTCOME_FAILED = "failed"  # the pipeline itself broke


@dataclass
class PassRate:
    """Passing criteria out of the total. ``rate`` is ``None`` when nothing ran at all."""

    total: int = 0
    passed: int = 0
    failed: int = 0

    @property
    def rate(self) -> float | None:
        # An empty run is *unknown*, not 100% and not 0% — either would be a lie in a report.
        return None if self.total == 0 else round(self.passed / self.total, 4)

    @property
    def green(self) -> bool:
        return self.total > 0 and self.failed == 0

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> PassRate:
        data = data or {}
        return cls(
            total=int(data.get("total") or 0),
            passed=int(data.get("passed") or 0),
            failed=int(data.get("failed") or 0),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "rate": self.rate,
        }


@dataclass
class SpecMetrics:
    """One spec's run. Every D13 field lives here."""

    spec_id: str
    title: str = ""
    difficulty: str = ""
    outcome: str = OUTCOME_FAILED

    # -- the core contribution evidence, captured distinctly --------------------------
    first_pass: PassRate = field(default_factory=PassRate)
    post_repair: PassRate = field(default_factory=PassRate)
    repair_iterations: int = 0
    repair_outcome: str | None = None
    regressions: int = 0

    # -- the same two measurements against the spec's acceptance criteria -----------
    criteria_total: int = 0
    #: Criteria the generated tests reference — what testgen wrote, before anything ran.
    criteria_covered: int = 0
    first_pass_criteria: PassRate = field(default_factory=PassRate)
    post_repair_criteria: PassRate = field(default_factory=PassRate)

    # -- cost + time ------------------------------------------------------------------
    tokens: int = 0
    inr_cost: float = 0.0
    #: Wall clock from the run starting to a live URL existing — the demo-able headline.
    #: ``None`` when the deploy leg did not run, never 0, which would read as "instant".
    screenshot_to_url_seconds: float | None = None
    wall_clock_seconds: float = 0.0
    stage_seconds: dict[str, float] = field(default_factory=dict)

    # -- provenance -------------------------------------------------------------------
    project_id: str | None = None
    live_url: str | None = None
    stages_run: list[str] = field(default_factory=list)
    #: The build stage's status once the build finished — `in_progress`/`empty` means it failed.
    build_status: str | None = None
    error: str | None = None

    @property
    def repair_delta(self) -> float | None:
        """How much the loop moved the needle — the single most quotable number."""
        if self.first_pass.rate is None or self.post_repair.rate is None:
            return None
        return round(self.post_repair.rate - self.first_pass.rate, 4)

    @property
    def criteria_repair_delta(self) -> float | None:
        first, post = self.first_pass_criteria.rate, self.post_repair_criteria.rate
        if first is None or post is None:
            return None
        return round(post - first, 4)

    @property
    def repaired(self) -> bool:
        """True when repair actually improved things (rather than merely running)."""
        delta = self.repair_delta
        return delta is not None and delta > 0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SpecMetrics:
        """Rebuild a record from its JSON (derived keys such as ``repair_delta`` are recomputed)."""
        rates = ("first_pass", "post_repair", "first_pass_criteria", "post_repair_criteria")
        values: dict[str, Any] = {
            f.name: data[f.name] for f in fields(cls) if f.name in data and f.name not in rates
        }
        values.update({key: PassRate.from_dict(data.get(key)) for key in rates})
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["first_pass"] = self.first_pass.to_dict()
        data["post_repair"] = self.post_repair.to_dict()
        data["first_pass_criteria"] = self.first_pass_criteria.to_dict()
        data["post_repair_criteria"] = self.post_repair_criteria.to_dict()
        data["repair_delta"] = self.repair_delta
        data["criteria_repair_delta"] = self.criteria_repair_delta
        data["repaired"] = self.repaired
        return data


@dataclass
class EvalReport:
    """A set of spec runs plus the aggregates a report or dashboard (phase-45) reads."""

    records: list[SpecMetrics] = field(default_factory=list)
    started_at: datetime = field(default_factory=utcnow)
    finished_at: datetime | None = None
    legs: list[str] = field(default_factory=list)

    def aggregate(self) -> dict[str, Any]:
        """Corpus-level numbers. Specs that never ran tests are excluded from the means, not
        counted as zero — otherwise a crashed spec would silently drag the headline down."""
        scored = [r for r in self.records if r.first_pass.rate is not None]
        first = [r.first_pass.rate for r in scored if r.first_pass.rate is not None]
        post = [r.post_repair.rate for r in scored if r.post_repair.rate is not None]
        graded = [r for r in self.records if r.first_pass_criteria.rate is not None]
        criteria_total = sum(r.criteria_total for r in graded)

        return {
            "specs": len(self.records),
            "scored": len(scored),
            "delivered": sum(1 for r in self.records if r.outcome == OUTCOME_DELIVERED),
            "escalated": sum(1 for r in self.records if r.outcome == OUTCOME_ESCALATED),
            "failed": sum(1 for r in self.records if r.outcome == OUTCOME_FAILED),
            "mean_first_pass_rate": _mean(first),
            "mean_post_repair_rate": _mean(post),
            "mean_repair_delta": _mean(
                [r.repair_delta for r in scored if r.repair_delta is not None]
            ),
            "specs_improved_by_repair": sum(1 for r in scored if r.repaired),
            "green_first_pass": sum(1 for r in scored if r.first_pass.green),
            "green_post_repair": sum(1 for r in scored if r.post_repair.green),
            "criteria_total": criteria_total,
            "criteria_coverage": (
                round(sum(r.criteria_covered for r in graded) / criteria_total, 4)
                if criteria_total
                else None
            ),
            "mean_first_pass_criteria_rate": _mean(
                [
                    r.first_pass_criteria.rate
                    for r in graded
                    if r.first_pass_criteria.rate is not None
                ]
            ),
            "mean_post_repair_criteria_rate": _mean(
                [
                    r.post_repair_criteria.rate
                    for r in graded
                    if r.post_repair_criteria.rate is not None
                ]
            ),
            "all_criteria_met_first_pass": sum(1 for r in graded if r.first_pass_criteria.green),
            "all_criteria_met_post_repair": sum(1 for r in graded if r.post_repair_criteria.green),
            "total_repair_iterations": sum(r.repair_iterations for r in self.records),
            "total_regressions": sum(r.regressions for r in self.records),
            "total_tokens": sum(r.tokens for r in self.records),
            "total_inr": round(sum(r.inr_cost for r in self.records), 4),
            "wall_clock_seconds": round(sum(r.wall_clock_seconds for r in self.records), 2),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "legs": self.legs,
            "aggregate": self.aggregate(),
            "records": [r.to_dict() for r in self.records],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def merge(cls, parts: list[dict[str, Any]]) -> EvalReport:
        """One report from several partial runs' JSON (a corpus split across processes)."""
        records = [SpecMetrics.from_dict(r) for part in parts for r in part.get("records", [])]
        started = [datetime.fromisoformat(p["started_at"]) for p in parts if p.get("started_at")]
        finished = [datetime.fromisoformat(p["finished_at"]) for p in parts if p.get("finished_at")]
        return cls(
            records=records,
            started_at=min(started) if started else utcnow(),
            finished_at=max(finished) if finished else None,
            legs=list(next((p["legs"] for p in parts if p.get("legs")), [])),
        )


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


__all__ = [
    "OUTCOME_DELIVERED",
    "OUTCOME_ESCALATED",
    "OUTCOME_FAILED",
    "EvalReport",
    "PassRate",
    "SpecMetrics",
]
