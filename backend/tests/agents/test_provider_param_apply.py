"""Applying a repair to the request body (phase-57).

`apply_repairs` is the only writer of the Chat Completions body, so the guarantees that matter are
about what it must *not* destroy: a rename must keep the value (an output cap that is silently
dropped is a cost regression disguised as a fix), and the role/stream repairs must not take the
transcript or the response path down with them.
"""

from __future__ import annotations

from typing import Any

from app.agents.provider_compat import Adaptation, Repair, apply_repairs, seed_repairs


def _body() -> dict[str, Any]:
    return {
        "model": "gpt-5.4-mini",
        "max_tokens": 4096,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": "be helpful"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ],
        "stream": True,
        "stream_options": {"include_usage": True},
    }


def test_a_rename_moves_the_value_and_does_not_lose_the_cap() -> None:
    """Dropping max_tokens would make the call succeed while uncapping spend (D13)."""
    out = apply_repairs(
        _body(), [Repair(Adaptation.rename, "max_tokens", replacement="max_completion_tokens")]
    )

    assert "max_tokens" not in out
    assert out["max_completion_tokens"] == 4096


def test_a_drop_removes_only_its_own_key() -> None:
    out = apply_repairs(_body(), [Repair(Adaptation.drop, "temperature")])

    assert "temperature" not in out
    assert out["max_tokens"] == 4096
    assert len(out["messages"]) == 3


def test_the_developer_role_repair_rewrites_only_the_system_message() -> None:
    out = apply_repairs(_body(), [Repair(Adaptation.developer_role, "messages")])

    assert [m["role"] for m in out["messages"]] == ["developer", "user", "assistant"]
    assert out["messages"][0]["content"] == "be helpful"  # content is untouched


def test_the_no_stream_repair_also_clears_the_usage_trailer() -> None:
    """`stream_options` is streaming-only; leaving it fails the retried call for a new reason."""
    out = apply_repairs(_body(), [Repair(Adaptation.no_stream, "stream")])

    assert out["stream"] is False
    assert "stream_options" not in out


def test_repairs_compose_to_the_same_body_in_any_order() -> None:
    repairs = [
        Repair(Adaptation.rename, "max_tokens", replacement="max_completion_tokens"),
        Repair(Adaptation.drop, "temperature"),
        Repair(Adaptation.developer_role, "messages"),
    ]

    forward = apply_repairs(_body(), repairs)
    backward = apply_repairs(_body(), list(reversed(repairs)))

    assert forward == backward


def test_the_original_body_is_never_mutated() -> None:
    """The transport keeps `params` across retries; an in-place edit corrupts the next attempt."""
    original = _body()
    apply_repairs(original, [Repair(Adaptation.drop, "temperature")])

    assert original["temperature"] == 0.2


def test_a_repair_for_an_absent_key_is_a_no_op() -> None:
    """A learned repair can outlive the parameter it was about; it must not raise."""
    out = apply_repairs({"model": "m"}, [Repair(Adaptation.rename, "max_tokens", replacement="x")])

    assert out == {"model": "m"}


# ---------------------------------------------------------------- the seed table


def test_reasoning_families_are_seeded_so_the_first_call_is_not_wasted() -> None:
    repairs = {r.signature for r in seed_repairs("gpt-5.4-mini")}

    assert ("rename", "max_tokens") in repairs
    assert ("drop", "temperature") in repairs


def test_the_seed_table_applies_to_the_known_reasoning_ids() -> None:
    for model in ("o1", "o3-mini", "gpt-5", "gpt-5.4-mini", "openai/gpt-5-nano"):
        assert seed_repairs(model), model


def test_ordinary_models_are_not_seeded() -> None:
    """A seed rule applied to a model that is fine would break a working provider."""
    for model in ("gpt-4o", "claude-sonnet-5", "mistral-large-latest", "llama-3.1-70b"):
        assert seed_repairs(model) == [], model


def test_a_seeded_body_is_the_one_the_reported_model_wanted() -> None:
    """End to end over the pure layer: seed rules alone fix the reported failure."""
    out = apply_repairs(_body(), seed_repairs("gpt-5.4-mini"))

    assert out["max_completion_tokens"] == 4096
    assert "max_tokens" not in out
    assert "temperature" not in out
