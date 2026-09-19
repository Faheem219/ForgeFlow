"""The phase-57 repair ladder sees inside ``extra_body`` (phase-64).

Provider-specific fields (OpenRouter's ``provider`` routing, the ``reasoning`` hint) cannot be
top-level kwargs — the OpenAI SDK's ``create()`` has a closed signature — so they travel in
``extra_body`` and are merged into the JSON body. A provider that rejects one names it by its
merged name. The ladder must recognise that as *ours* and drop it from where it actually lives.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.agents.provider_compat import Adaptation, Repair, apply_repairs, diagnose_param_error

openai_sdk = pytest.importorskip("openai", reason="the openai SDK is optional")

SENT: dict[str, Any] = {
    "model": "z-ai/glm-5.3-flash",
    "max_tokens": 16384,
    "messages": [{"role": "user", "content": "hi"}],
    "stream": True,
    "extra_body": {"reasoning": {"effort": "low"}, "provider": {"sort": "price"}},
}


def _error(status: int, message: str, body: dict[str, Any] | None = None) -> Any:
    response = httpx.Response(status, request=httpx.Request("POST", "https://x.example/v1"))
    return openai_sdk.APIStatusError(message, response=response, body=body)


def test_a_rejected_extra_body_field_is_diagnosed_as_a_drop() -> None:
    exc = _error(
        400,
        "Unrecognized request argument supplied: reasoning",
        body={"message": "Unrecognized request argument supplied: reasoning", "param": "reasoning"},
    )
    repair = diagnose_param_error(exc, SENT)
    assert repair is not None
    assert repair.adaptation is Adaptation.drop and repair.param == "reasoning"


def test_a_field_named_in_neither_place_is_not_ours() -> None:
    exc = _error(400, "unknown parameter: thinking", body={"param": "thinking"})
    assert diagnose_param_error(exc, SENT) is None


def test_drop_removes_the_field_from_extra_body_and_keeps_the_rest() -> None:
    out = apply_repairs(SENT, [Repair(Adaptation.drop, param="reasoning", reason="rejected")])
    assert out["extra_body"] == {"provider": {"sort": "price"}}
    assert "reasoning" not in out
    assert SENT["extra_body"]["reasoning"] == {"effort": "low"}  # the input is never mutated


def test_dropping_the_last_extra_body_field_removes_extra_body_itself() -> None:
    sent = {**SENT, "extra_body": {"reasoning": {"effort": "low"}}}
    out = apply_repairs(sent, [Repair(Adaptation.drop, param="reasoning", reason="rejected")])
    assert "extra_body" not in out
