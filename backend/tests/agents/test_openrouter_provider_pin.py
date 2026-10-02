"""OPENROUTER_PROVIDER_ORDER pins OpenRouter to named upstreams; blank keeps price routing."""

from __future__ import annotations

import pytest

from app.agents.anthropic_client import (
    MessageRequest,
    build_openai_params,
    openrouter_provider_preference,
)
from app.core.config import reset_config

OPENROUTER = "https://openrouter.ai/api/v1"


def _request() -> MessageRequest:
    return MessageRequest(
        model="z-ai/glm-5.3-flash",
        system=None,
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        max_tokens=256,
    )


def test_blank_keeps_lowest_price_routing() -> None:
    assert openrouter_provider_preference("") == {"sort": "price"}
    assert openrouter_provider_preference(" , ") == {"sort": "price"}


def test_a_pin_names_upstreams_in_order_with_no_fallback() -> None:
    assert openrouter_provider_preference("z-ai, fireworks") == {
        "order": ["z-ai", "fireworks"],
        "allow_fallbacks": False,
    }


def test_the_pin_reaches_the_request_body(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_PROVIDER_ORDER", "z-ai")
    reset_config()

    params = build_openai_params(_request(), endpoint=OPENROUTER)

    assert params["extra_body"]["provider"] == {"order": ["z-ai"], "allow_fallbacks": False}


def test_other_endpoints_never_get_a_provider_object(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_PROVIDER_ORDER", "z-ai")
    reset_config()

    params = build_openai_params(_request(), endpoint="https://api.openai.com/v1")

    assert "provider" not in params.get("extra_body", {})
