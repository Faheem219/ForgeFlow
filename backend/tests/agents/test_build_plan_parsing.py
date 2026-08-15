"""The phase plan parses, validates and — above all — always yields something runnable (phase-56).

`fallback_plan` is mandatory rather than defensive: D12 says a build must work with requirements
only, design only, both, or neither, so a plan step that could return nothing would break the
invariant. Every unusable model output below lands on the deterministic plan instead.
"""

from __future__ import annotations

import json

from app.agents.build_plan import (
    PHASE_DONE,
    SOURCE_FALLBACK,
    SOURCE_MODEL,
    fallback_plan,
    parse_build_plan,
    render_markdown,
)

MAX = 8


def _plan_json(phases: list[dict[str, object]]) -> str:
    return json.dumps({"phases": phases, "assumptions": [], "notes": []})


def test_clean_json_parses() -> None:
    text = _plan_json(
        [
            {"id": "be", "title": "API", "kind": "backend", "goal": "routes", "done_when": ["ok"]},
            {"id": "fe", "title": "UI", "kind": "frontend", "goal": "pages", "depends_on": ["be"]},
        ]
    )
    plan = parse_build_plan(text)
    assert plan is not None
    plan = plan.validate(max_phases=MAX)
    assert [p.id for p in plan.phases] == ["be", "fe"]
    assert plan.source == SOURCE_MODEL
    assert plan.phases[1].depends_on == ["be"]


def test_fenced_and_prose_wrapped_json_parse() -> None:
    body = _plan_json([{"id": "be", "title": "API", "kind": "backend", "goal": "g"}])
    for text in (
        f"```json\n{body}\n```",
        f"Here is the plan:\n{body}\nThat's everything.",
        f"```\n{body}\n```",
    ):
        plan = parse_build_plan(text)
        assert plan is not None, text
        assert plan.phases[0].id == "be"


def test_garbage_yields_none_so_the_caller_falls_back() -> None:
    for text in ("", "no json here", "{", "[]", '{"phases": []}', '{"phases": "nope"}'):
        assert parse_build_plan(text) is None


def test_more_phases_than_the_cap_are_truncated() -> None:
    plan = parse_build_plan(
        _plan_json(
            [{"id": f"p{i}", "title": f"P{i}", "kind": "backend", "goal": "g"} for i in range(20)]
        )
    )
    assert plan is not None
    assert len(plan.validate(max_phases=MAX).phases) == MAX


def test_duplicate_ids_are_dropped() -> None:
    plan = parse_build_plan(
        _plan_json(
            [
                {"id": "be", "title": "First", "kind": "backend", "goal": "g"},
                {"id": "be", "title": "Duplicate", "kind": "backend", "goal": "g"},
            ]
        )
    )
    assert plan is not None
    validated = plan.validate(max_phases=MAX)
    assert [p.title for p in validated.phases] == ["First"]


def test_a_dependency_cycle_still_produces_a_runnable_plan() -> None:
    """A cycle cannot be ordered — but it must not be able to block the build either."""
    plan = parse_build_plan(
        _plan_json(
            [
                {"id": "a", "title": "A", "kind": "backend", "goal": "g", "depends_on": ["b"]},
                {"id": "b", "title": "B", "kind": "backend", "goal": "g", "depends_on": ["a"]},
            ]
        )
    )
    assert plan is not None
    validated = plan.validate(max_phases=MAX)
    assert {p.id for p in validated.phases} == {"a", "b"}  # nothing was silently lost


def test_phases_are_topologically_ordered() -> None:
    plan = parse_build_plan(
        _plan_json(
            [
                {"id": "fe", "title": "UI", "kind": "frontend", "goal": "g", "depends_on": ["be"]},
                {"id": "be", "title": "API", "kind": "backend", "goal": "g"},
            ]
        )
    )
    assert plan is not None
    assert [p.id for p in plan.validate(max_phases=MAX).phases] == ["be", "fe"]


def test_an_unknown_kind_degrades_rather_than_failing() -> None:
    plan = parse_build_plan(_plan_json([{"id": "x", "title": "X", "kind": "quantum", "goal": "g"}]))
    assert plan is not None
    assert plan.phases[0].kind == "backend"


def test_a_dangling_dependency_cannot_strand_a_phase() -> None:
    plan = parse_build_plan(
        _plan_json(
            [{"id": "fe", "title": "UI", "kind": "frontend", "goal": "g", "depends_on": ["ghost"]}]
        )
    )
    assert plan is not None
    validated = plan.validate(max_phases=MAX)
    assert validated.phases[0].depends_on == []


def test_the_fallback_plan_works_with_no_upstream_artifacts() -> None:
    """D12: requirements-only, design-only, both or NEITHER must all build."""
    plan = fallback_plan([], has_design=False).validate(max_phases=MAX)
    assert plan.source == SOURCE_FALLBACK
    assert [p.id for p in plan.phases] == ["be-core", "fe-core", "wiring"]
    assert plan.phases[-1].kind == "wiring"  # the placeholder rules always land last
    assert plan.assumptions  # and it says what it assumed


def test_the_fallback_plan_names_the_features_it_was_given() -> None:
    plan = fallback_plan(["Todos", "Notes"], has_design=True)
    assert "Todos" in plan.phases[0].goal
    assert not any("design artifact" in a for a in plan.assumptions)


def test_the_typecheck_gate_is_scoped_per_kind() -> None:
    plan = fallback_plan(["Todos"], has_design=False)
    packages = {p.kind: p.package for p in plan.phases}
    assert packages["backend"] == "backend"
    assert packages["frontend"] == "frontend"
    assert packages["wiring"] is None  # wiring legitimately touches both halves


def test_prose_and_markdown_renderings_are_usable() -> None:
    plan = fallback_plan(["Todos"], has_design=False)
    assert "Backend data models" in plan.to_prose()
    md = render_markdown(plan, {"be-core": PHASE_DONE})
    assert "- [x]" in md  # the completed phase is ticked
    assert "- [ ]" in md  # …and the rest are not
