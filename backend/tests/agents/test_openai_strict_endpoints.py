"""Strict OpenAI-compatible endpoints (Mistral, Azure serverless) must not fail on every call.

`stream_options: {"include_usage": true}` is an optimisation — it asks for a token-usage trailer
so cost accounting has real numbers. Mistral's schema has no such field and answers
`422 extra_forbidden` ("Extra inputs are not permitted"), so sending it unconditionally makes
*every* request to that provider fail. The transport drops it for endpoints that refuse it and
keeps going; usage then reads zero there, a far better trade than not working at all.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from app.agents.anthropic_client import (
    MessageRequest,
    _rejects_stream_options,
    build_openai_params,
)

openai_sdk = pytest.importorskip("openai", reason="the openai SDK is optional")


@pytest.fixture(autouse=True)
def _clear_memo() -> Iterator[None]:
    from app.agents import anthropic_client

    anthropic_client._NO_STREAM_USAGE.clear()
    yield
    anthropic_client._NO_STREAM_USAGE.clear()


def _request(**overrides: Any) -> MessageRequest:
    base: dict[str, Any] = {
        "model": "mistral-large-latest",
        "system": "be helpful",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [],
        "max_tokens": 4096,
    }
    base.update(overrides)
    return MessageRequest(**base)


def _status_error(status: int, message: str) -> Any:
    response = httpx.Response(status, request=httpx.Request("POST", "https://api.mistral.ai/v1"))
    return openai_sdk.APIStatusError(message, response=response, body=None)


# ---------------------------------------------------------------- the request body


def test_the_usage_trailer_is_requested_by_default() -> None:
    params = build_openai_params(_request())
    assert params["stream_options"] == {"include_usage": True}
    assert params["stream"] is True


def test_a_strict_endpoint_gets_a_body_it_accepts() -> None:
    """Everything left must be a documented Mistral chat-completions field."""
    params = build_openai_params(_request(), stream_usage=False)

    assert "stream_options" not in params
    mistral_fields = {"model", "messages", "max_tokens", "stream", "tools", "tool_choice"}
    assert set(params) <= mistral_fields


def test_tools_are_translated_for_a_strict_endpoint() -> None:
    params = build_openai_params(
        _request(
            tools=[
                {
                    "name": "write_file",
                    "description": "Write a file",
                    "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
                }
            ]
        ),
        stream_usage=False,
    )

    assert params["tools"][0]["type"] == "function"
    assert params["tools"][0]["function"]["name"] == "write_file"
    assert params["tools"][0]["function"]["parameters"]["properties"]["path"]


# ---------------------------------------------------------------- classifying the refusal


@pytest.mark.parametrize(
    "message",
    [
        # Mistral's actual 422 body.
        'Error code: 422 - {"detail":[{"type":"extra_forbidden","loc":["body","stream_options"],'
        '"msg":"Extra inputs are not permitted"}]}',
        "Extra inputs are not permitted: stream_options",
        "Unknown parameter: 'stream_options'.",
        "stream_options.include_usage is not supported",
    ],
)
def test_a_refused_usage_trailer_is_recognised(message: str) -> None:
    assert _rejects_stream_options(_status_error(422, message)) is True


@pytest.mark.parametrize(
    "message",
    [
        "Rate limit exceeded, please try again",
        "This model does not support tool use",
        "Invalid API key provided",
        "The server is busy right now",
    ],
)
def test_unrelated_failures_are_not_mistaken_for_it(message: str) -> None:
    """A wrong guess here would silently disable usage accounting for a healthy provider."""
    assert _rejects_stream_options(_status_error(422, message)) is False


def test_a_server_error_naming_stream_options_is_not_treated_as_a_schema_refusal() -> None:
    """A 500 is an outage, not a rejected field — that path must stay with the transient retries."""
    assert _rejects_stream_options(_status_error(500, "stream_options blew up")) is False
