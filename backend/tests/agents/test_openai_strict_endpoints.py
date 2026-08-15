"""Strict OpenAI-compatible endpoints (Mistral, Azure serverless) must not fail on every call.

`stream_options: {"include_usage": true}` is an optimisation — it asks for a token-usage trailer
so cost accounting has real numbers. Mistral's schema has no such field and answers
`422 extra_forbidden` ("Extra inputs are not permitted"), so sending it unconditionally makes
*every* request to that provider fail. The transport drops it for endpoints that refuse it and
keeps going; usage then reads zero there, a far better trade than not working at all.

**phase-57 note.** This behaviour was originally a bespoke predicate (`_rejects_stream_options`) and
an endpoint-keyed memo, built for this one parameter. It is now one instance of the general
compatibility mechanism (`app.agents.provider_compat`), which reads whatever parameter the provider
names. These tests are kept as the regression guard that generalising did not lose the specific
case — the guarantees are unchanged, only the route to them is.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.agents.anthropic_client import MessageRequest, build_openai_params
from app.agents.provider_compat import Adaptation, apply_repairs, diagnose_param_error

openai_sdk = pytest.importorskip("openai", reason="the openai SDK is optional")


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


def _drop_stream_options(request: MessageRequest) -> dict[str, Any]:
    """The body a strict endpoint gets, once the transport has learned it refuses the trailer."""
    params = build_openai_params(request)
    repair = diagnose_param_error(
        _status_error(422, "Extra inputs are not permitted: stream_options"), params
    )
    assert repair is not None and repair.adaptation is Adaptation.drop
    return apply_repairs(params, [repair])


# ---------------------------------------------------------------- the request body


def test_the_usage_trailer_is_requested_by_default() -> None:
    params = build_openai_params(_request())
    assert params["stream_options"] == {"include_usage": True}
    assert params["stream"] is True


def test_a_strict_endpoint_gets_a_body_it_accepts() -> None:
    """Everything left must be a documented Mistral chat-completions field."""
    params = _drop_stream_options(_request())

    assert "stream_options" not in params
    mistral_fields = {"model", "messages", "max_tokens", "stream", "tools", "tool_choice"}
    assert set(params) <= mistral_fields


def test_tools_are_translated_for_a_strict_endpoint() -> None:
    params = _drop_stream_options(
        _request(
            tools=[
                {
                    "name": "write_file",
                    "description": "Write a file",
                    "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
                }
            ]
        )
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
    params = build_openai_params(_request())

    repair = diagnose_param_error(_status_error(422, message), params)

    assert repair is not None
    assert repair.adaptation is Adaptation.drop
    assert repair.param == "stream_options"


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
    params = build_openai_params(_request())

    repair = diagnose_param_error(_status_error(422, message), params)

    assert repair is None or repair.param != "stream_options"


def test_a_server_error_naming_stream_options_is_not_treated_as_a_schema_refusal() -> None:
    """A 500 is an outage, not a rejected field — that path must stay with the transient retries."""
    params = build_openai_params(_request())

    assert diagnose_param_error(_status_error(500, "stream_options blew up"), params) is None
