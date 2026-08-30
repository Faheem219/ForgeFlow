"""OpenRouter gets a lowest-price provider-routing preference; nothing else changes.

OpenRouter fronts many upstreams for the same model id and lets the caller state a preference via
a `provider` field on the request body (https://openrouter.ai/docs/features/provider-routing).
Asking it to sort by price is free money under golden rule 7 (cost discipline).

The whole risk of that field is sending it somewhere it is not understood — OpenAI, Azure, a local
vLLM — so these tests are mostly about the *negative* case: for every other base URL the body must
be byte-identical to the one built before this existed.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from app.agents.anthropic_client import MessageRequest, build_openai_params, is_openrouter

openai_sdk = pytest.importorskip("openai", reason="the openai SDK is optional")

#: `sort: "price"` is OpenRouter's "prioritise lowest price" — cheapest upstream first.
PRICE_ROUTING = {"sort": "price"}


def _request(**overrides: Any) -> MessageRequest:
    base: dict[str, Any] = {
        "model": "anthropic/claude-sonnet-5",
        "system": "be helpful",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [],
        "max_tokens": 4096,
    }
    base.update(overrides)
    return MessageRequest(**base)


# ---------------------------------------------------------------- detection


@pytest.mark.parametrize(
    "base_url",
    [
        "https://openrouter.ai/api/v1",
        "https://openrouter.ai/api/v1/",
        "http://openrouter.ai/api/v1",
        "HTTPS://OpenRouter.AI/api/v1",
        "openrouter.ai/api/v1",  # configured without a scheme
        "  https://openrouter.ai/api/v1  ",
        "https://gateway.openrouter.ai/api/v1",  # a subdomain is still OpenRouter
    ],
)
def test_openrouter_base_urls_are_detected(base_url: str) -> None:
    assert is_openrouter(base_url) is True


@pytest.mark.parametrize(
    "base_url",
    [
        "",
        "   ",
        "default",  # the transport's placeholder for "the SDK's own default endpoint"
        "https://api.openai.com/v1",
        "https://example.openai.azure.com/openai/v1",
        "http://localhost:11434/v1",  # Ollama
        "https://api.mistral.ai/v1",
        "https://api.together.xyz/v1",
        # Lookalikes: the host is what counts, not the string.
        "https://proxy.example.com/openrouter.ai/v1",
        "https://example.com/v1?upstream=openrouter.ai",
        "https://openrouter.ai.evil.example.com/v1",
        "https://not-openrouter.ai/v1",
    ],
)
def test_other_base_urls_are_not_detected(base_url: str) -> None:
    assert is_openrouter(base_url) is False


# ---------------------------------------------------------------- the request body


def test_openrouter_body_carries_the_price_sort() -> None:
    params = build_openai_params(_request(), endpoint="https://openrouter.ai/api/v1")
    assert params["extra_body"]["provider"] == PRICE_ROUTING


def test_the_params_are_accepted_by_the_sdk_call_signature() -> None:
    """The regression guard for *how* the field travels.

    `OpenAITransport.stream` calls `client.chat.completions.create(**params)`, and that method has
    a closed signature — no `**kwargs`. A vendor field passed as a top-level kwarg raises
    `TypeError` before a request is ever made, which no pure body-shape assertion would catch: it
    would break every OpenRouter call while the wire format looked perfectly correct.
    """
    create = openai_sdk.resources.chat.completions.AsyncCompletions.create
    signature = inspect.signature(create)
    for endpoint in ("", "https://openrouter.ai/api/v1", "https://api.openai.com/v1"):
        params = build_openai_params(_request(), endpoint=endpoint)
        signature.bind(None, **params)  # raises TypeError on an unroutable kwarg


def test_the_price_sort_is_the_only_difference() -> None:
    """Everything else about the OpenRouter body is what every other endpoint gets."""
    endpoint = "https://openrouter.ai/api/v1"
    with_routing = build_openai_params(_request(), endpoint=endpoint)
    baseline = build_openai_params(_request())
    assert with_routing.pop("extra_body") == {"provider": PRICE_ROUTING}
    assert with_routing == baseline


@pytest.mark.parametrize(
    "endpoint",
    ["", "default", "https://api.openai.com/v1", "http://localhost:11434/v1"],
)
def test_other_endpoints_get_an_unchanged_body(endpoint: str) -> None:
    assert "extra_body" not in build_openai_params(_request(), endpoint=endpoint)
    assert build_openai_params(_request(), endpoint=endpoint) == build_openai_params(_request())


def test_the_default_endpoint_argument_changes_nothing() -> None:
    """Callers that never pass `endpoint` (and the existing tests) are untouched."""
    assert "extra_body" not in build_openai_params(_request())


def test_tool_requests_are_routed_by_price_too() -> None:
    tools = [{"name": "write_file", "description": "d", "input_schema": {"type": "object"}}]
    params = build_openai_params(_request(tools=tools), endpoint="https://openrouter.ai/api/v1")
    assert params["extra_body"]["provider"] == PRICE_ROUTING
    assert params["tools"]


def test_the_repair_ladder_still_reaches_the_real_parameters() -> None:
    """`extra_body` must not shadow the phase-57 repairs, which act on top-level wire params."""
    from app.agents.provider_compat import Adaptation, Repair, apply_repairs

    params = build_openai_params(_request(), endpoint="https://openrouter.ai/api/v1")
    repaired = apply_repairs(
        params,
        [
            Repair(Adaptation.drop, param="stream_options"),
            Repair(Adaptation.rename, param="max_tokens", replacement="max_completion_tokens"),
        ],
    )
    assert "stream_options" not in repaired
    assert repaired["max_completion_tokens"] == 4096
    assert repaired["extra_body"]["provider"] == PRICE_ROUTING
