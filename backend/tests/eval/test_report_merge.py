"""A corpus split across processes merges into one report, losing nothing."""

from __future__ import annotations

import json

from app.eval.metrics import EvalReport, PassRate, SpecMetrics


def _record(spec_id: str, first: tuple[int, int], post: tuple[int, int]) -> SpecMetrics:
    m = SpecMetrics(
        spec_id=spec_id, title=spec_id.title(), difficulty="simple", outcome="delivered"
    )
    m.first_pass = PassRate(total=sum(first), passed=first[0], failed=first[1])
    m.post_repair = PassRate(total=sum(post), passed=post[0], failed=post[1])
    m.criteria_total, m.criteria_covered = 4, 4
    m.first_pass_criteria = PassRate(total=4, passed=2, failed=2)
    m.post_repair_criteria = PassRate(total=4, passed=4, failed=0)
    m.tokens, m.inr_cost, m.repair_iterations = 1000, 1.5, 2
    m.stage_seconds = {"build": 12.0}
    m.stages_run = ["requirements", "build"]
    return m


def test_a_record_survives_a_json_round_trip() -> None:
    original = _record("todo-list", (3, 2), (5, 0))
    data = json.loads(json.dumps(original.to_dict()))

    assert SpecMetrics.from_dict(data).to_dict() == original.to_dict()


def test_parts_merge_in_order_with_the_full_time_span_and_fresh_aggregates() -> None:
    a = EvalReport(records=[_record("a", (1, 1), (2, 0))], legs=["build", "test"])
    b = EvalReport(records=[_record("b", (2, 0), (2, 0))], legs=["build", "test"])
    a.finished_at = a.started_at
    b.finished_at = b.started_at
    parts = [json.loads(b.to_json()), json.loads(a.to_json())]

    merged = EvalReport.merge(parts)

    assert [r.spec_id for r in merged.records] == ["b", "a"]
    assert merged.legs == ["build", "test"]
    assert merged.started_at == min(a.started_at, b.started_at)
    assert merged.finished_at == max(a.finished_at, b.finished_at)
    agg = merged.aggregate()
    assert agg["specs"] == 2 and agg["total_tokens"] == 2000
    assert agg["mean_post_repair_criteria_rate"] == 1.0
