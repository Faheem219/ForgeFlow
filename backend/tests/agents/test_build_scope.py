"""Scope routing: a one-line refine must not re-plan the whole build (phase-60).

Reported: *"The home page just has a sign in prompt but no actual button to help me sign in, please
fix"* produced a full phase plan and a phase-by-phase execution. Scope is a property of the request,
so a cheap classify call decides — with a deterministic fallback for every way that call can fail,
because a build must never be blocked on a classifier.
"""

from __future__ import annotations

import pytest

from app.agents.build_scope import (
    BuildScope,
    ScopeClassifier,
    ScopeDecision,
    heuristic_scope,
    parse_scope,
    user_prompt,
)
from app.agents.models import TaskKind
from app.core.errors import UserError


class _FakeResult:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeClient:
    """Records the loop it was asked to run; returns a canned verdict or raises."""

    def __init__(
        self, text: str = "SMALL\na contained fix", error: Exception | None = None
    ) -> None:
        self._text = text
        self._error = error
        self.calls: list[dict[str, object]] = []

    async def run_tool_loop(self, **kwargs: object) -> _FakeResult:
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return _FakeResult(self._text)


def _classifier(**kwargs: object) -> tuple[ScopeClassifier, _FakeClient]:
    client = _FakeClient(**kwargs)  # type: ignore[arg-type]
    return ScopeClassifier(client), client


async def _decide(
    classifier: ScopeClassifier, instruction: str | None, **kw: object
) -> ScopeDecision:
    """Drive one decision. ``run`` is opaque to the classifier (it only forwards it to the loop),
    so a stub keeps these tests off the database entirely."""
    from types import SimpleNamespace

    from beanie import PydanticObjectId

    project_id = PydanticObjectId()
    return await classifier.decide(
        project_id=project_id,
        run=SimpleNamespace(id=None, kind="codegen:build"),  # type: ignore[arg-type]
        channel=str(project_id),
        instruction=instruction,
        has_build=bool(kw.pop("has_build", True)),
        **kw,  # type: ignore[arg-type]
    )


# --------------------------------------------------------------------- parsing


@pytest.mark.parametrize(
    "text",
    ["SMALL\na contained fix", "small", "**SMALL**\nreason", "Answer: SMALL", "SMALL."],
)
def test_a_small_verdict_is_read_in_any_reasonable_shape(text: str) -> None:
    decision = parse_scope(text)

    assert decision is not None and decision.scope is BuildScope.small
    assert decision.classified is True


@pytest.mark.parametrize("text", ["LARGE\nspans the app", "large", "**LARGE**"])
def test_a_large_verdict_is_read(text: str) -> None:
    decision = parse_scope(text)

    assert decision is not None and decision.scope is BuildScope.large


def test_a_reply_naming_both_is_no_verdict() -> None:
    """Ambiguity must fall back, not be resolved by which word came first."""
    assert parse_scope("SMALL or LARGE, hard to say") is None


def test_an_empty_or_off_script_reply_is_no_verdict() -> None:
    assert parse_scope("") is None
    assert parse_scope("I need more information about the app.") is None


def test_the_reason_is_carried_and_bounded() -> None:
    decision = parse_scope("SMALL\n" + "x" * 500)

    assert decision is not None
    assert 0 < len(decision.reason) <= 200


def test_a_verdict_without_a_reason_still_gets_one() -> None:
    decision = parse_scope("SMALL")

    assert decision is not None and decision.reason


# --------------------------------------------------------------------- heuristics (no model call)


def test_an_initial_build_always_plans() -> None:
    decision = heuristic_scope(None, has_build=False, fresh=False)

    assert decision is not None and decision.scope is BuildScope.large
    assert decision.classified is False


def test_a_blank_instruction_always_plans() -> None:
    assert heuristic_scope("   ", has_build=True, fresh=False) is not None


def test_an_explicit_rebuild_always_plans() -> None:
    decision = heuristic_scope("tweak the button", has_build=True, fresh=True)

    assert decision is not None and decision.scope is BuildScope.large


def test_a_refine_with_no_existing_build_plans() -> None:
    decision = heuristic_scope("tweak the button", has_build=False, fresh=False)

    assert decision is not None and decision.scope is BuildScope.large


def test_a_refine_on_a_built_project_asks_the_classifier() -> None:
    assert heuristic_scope("tweak the button", has_build=True, fresh=False) is None


def test_an_explicit_override_short_circuits_everything() -> None:
    decision = heuristic_scope(
        "tweak the button", has_build=True, fresh=False, forced=BuildScope.large
    )

    assert decision is not None and decision.scope is BuildScope.large
    assert decision.classified is False


# --------------------------------------------------------------------- the classifier call


async def test_the_reported_instruction_routes_to_a_single_pass() -> None:
    classifier, client = _classifier(text="SMALL\nonly the home page component changes")

    decision = await _decide(
        classifier,
        "The home page just has a sign in prompt but no actual button to help me sign in, "
        "please fix",
    )

    assert decision.scope is BuildScope.small
    assert len(client.calls) == 1


async def test_classification_uses_the_cheap_model_and_no_tools() -> None:
    """Cost discipline: a one-word verdict must never reach MODEL_CODEGEN or a tool loop."""
    classifier, client = _classifier()

    await _decide(classifier, "fix the button")

    call = client.calls[0]
    assert call["task_kind"] is TaskKind.classify
    assert call["tools"] == []
    assert call["max_turns"] == 1


async def test_a_cross_cutting_request_still_plans() -> None:
    classifier, _ = _classifier(text="LARGE\ntouches every page")

    decision = await _decide(classifier, "add dark mode across the whole app")

    assert decision.scope is BuildScope.large


async def test_a_classifier_failure_falls_back_to_planning() -> None:
    """A build must never be blocked on the classifier."""
    classifier, _ = _classifier(error=UserError("budget exhausted"))

    decision = await _decide(classifier, "fix the button")

    assert decision.scope is BuildScope.large
    assert decision.classified is False


async def test_an_unparseable_reply_falls_back_to_planning() -> None:
    classifier, _ = _classifier(text="It depends on what you mean.")

    decision = await _decide(classifier, "fix the button")

    assert decision.scope is BuildScope.large
    assert decision.classified is False


async def test_the_heuristic_path_makes_no_model_call_at_all() -> None:
    classifier, client = _classifier()

    await _decide(classifier, None, has_build=False)

    assert client.calls == []


def test_the_prompt_carries_the_instruction_and_the_app_size() -> None:
    prompt = user_prompt("fix the sign-in button", existing_files=12)

    assert "fix the sign-in button" in prompt
    assert "12" in prompt
