"""The completion gate: `complete` requires a *working* app, not just a boot (phase-55 task 13).

complete ⟺ typecheck_ok ∧ no placeholder ∧ fe running ∧ be running. Any single missing condition
holds the build for the user with a precise stop_reason. Pure truth-table over `_gate_stop_reason`.
"""

from __future__ import annotations

import pytest

from app.orchestrator.stages.build_verify import (
    STOP_BOOT,
    STOP_PLACEHOLDER,
    STOP_TYPECHECK,
    _gate_stop_reason,
)


def test_all_conditions_met_is_complete() -> None:
    assert _gate_stop_reason(True, True, True, True) is None


@pytest.mark.parametrize(
    ("typecheck_ok", "placeholder_ok", "fe_ok", "be_ok", "expected"),
    [
        (False, True, True, True, STOP_TYPECHECK),
        (True, False, True, True, STOP_PLACEHOLDER),
        (True, True, False, True, STOP_BOOT),
        (True, True, True, False, STOP_BOOT),
    ],
)
def test_each_single_missing_condition_holds(
    typecheck_ok: bool,
    placeholder_ok: bool,
    fe_ok: bool,
    be_ok: bool,
    expected: str,
) -> None:
    assert _gate_stop_reason(typecheck_ok, placeholder_ok, fe_ok, be_ok) == expected


def test_typecheck_is_reported_first_when_several_conditions_fail() -> None:
    # Deterministic precedence, so the message names the most fundamental problem.
    assert _gate_stop_reason(False, False, False, False) == STOP_TYPECHECK
    assert _gate_stop_reason(True, False, False, False) == STOP_PLACEHOLDER
