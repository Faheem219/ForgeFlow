"""Live subset selection (phase-39).

Production runs the smoke/critical slice — but only when the generated suite actually carries those
tags. The failure mode this guards against is subtle and dangerous: selecting a tag no test has
would run **zero** tests, and a zero-test run looks exactly like a pass.
"""

from __future__ import annotations

from app.testing.live import CRITICAL_TAG, SMOKE_TAG, select_live_subset

_TAGGED = {
    "frontend/e2e/todos.spec.ts": (
        "test('[ac-list] @smoke shows todos', async ({ page }) => {});\n"
        "test('[ac-add] @critical adds a todo', async ({ page }) => {});\n"
        "test('[ac-sort] reorders todos', async ({ page }) => {});\n"
    )
}
_UNTAGGED = {
    "frontend/e2e/todos.spec.ts": "test('[ac-list] shows todos', async ({ page }) => {});\n"
}


def test_both_selects_smoke_and_critical() -> None:
    selection = select_live_subset("both", _TAGGED)

    assert selection.grep == f"{SMOKE_TAG}|{CRITICAL_TAG}"
    assert selection.tags == (SMOKE_TAG, CRITICAL_TAG)
    assert not selection.full_suite


def test_smoke_only_selects_smoke() -> None:
    selection = select_live_subset("smoke", _TAGGED)

    assert selection.grep == SMOKE_TAG
    assert CRITICAL_TAG not in (selection.grep or "")


def test_critical_only_selects_critical() -> None:
    selection = select_live_subset("critical", _TAGGED)
    assert selection.grep == CRITICAL_TAG


def test_all_runs_the_whole_suite_unfiltered() -> None:
    selection = select_live_subset("all", _TAGGED)

    assert selection.full_suite
    assert "configured" in selection.reason


def test_an_untagged_suite_runs_in_full_rather_than_empty() -> None:
    """The important one: no tags must mean *everything*, never *nothing*."""
    selection = select_live_subset("both", _UNTAGGED)

    assert selection.full_suite
    assert "no @smoke or @critical" in selection.reason


def test_only_the_tags_actually_present_are_selected() -> None:
    """Asking for both when only smoke exists must not grep for a tag nothing carries."""
    smoke_only = {"frontend/e2e/a.spec.ts": "test('[ac-1] @smoke loads', () => {});"}

    selection = select_live_subset("both", smoke_only)

    assert selection.grep == SMOKE_TAG
    assert selection.tags == (SMOKE_TAG,)


def test_no_specs_at_all_falls_back_to_the_full_suite() -> None:
    assert select_live_subset("both", {}).full_suite


def test_an_unknown_selection_value_behaves_like_both() -> None:
    assert select_live_subset("nonsense", _TAGGED).grep == f"{SMOKE_TAG}|{CRITICAL_TAG}"
