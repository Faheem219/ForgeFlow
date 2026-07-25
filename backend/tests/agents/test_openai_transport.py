"""OpenAI-compatible transport (phase-53).

The agent loop's internal vocabulary is Anthropic-native; `OpenAITransport` is a pure adapter that
translates that shape to/from Chat Completions. These tests pin the two risky halves of that adapter
— the request translation and the streamed-response reassembly (tool calls arrive fragmented, keyed
by index) — plus provider selection. The real network path is not exercised (no SDK/creds here),
exactly like the Anthropic `SdkTransport`.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from types import SimpleNamespace
from typing import Any

import pytest

from app.agents.anthropic_client import (
    MessageRequest,
    OpenAITransport,
    SdkTransport,
    TextDelta,
    TurnComplete,
    _accumulate,
    _backoff_delay,
    _is_transient_openai_error,
    _parse_tool_args,
    _provider_message,
    _to_openai_messages,
    _to_openai_tools,
    default_transport,
)
from app.core.config import reset_config
from app.core.errors import ProviderError, SystemError


@pytest.fixture(autouse=True)
def _isolate_config() -> Iterator[None]:
    # default_transport() reads cached config; reset after each test so provider env can't leak.
    yield
    reset_config()


async def _aiter(items: list[Any]) -> AsyncIterator[Any]:
    for item in items:
        yield item


# --------------------------------------------------------------------- request translation


def test_to_openai_tools_maps_input_schema_to_function_parameters() -> None:
    schema = {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}
    out = _to_openai_tools([{"name": "write_file", "description": "Write", "input_schema": schema}])
    assert out == [
        {
            "type": "function",
            "function": {"name": "write_file", "description": "Write", "parameters": schema},
        }
    ]


def test_to_openai_messages_translates_a_full_tool_turn() -> None:
    convo: list[dict[str, Any]] = [
        {"role": "user", "content": "build a todo app"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "on it"},
                {"type": "tool_use", "id": "t1", "name": "write_file", "input": {"path": "a.ts"}},
            ],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}],
        },
    ]

    out = _to_openai_messages("SYSTEM", convo)

    assert out[0] == {"role": "system", "content": "SYSTEM"}
    assert out[1] == {"role": "user", "content": "build a todo app"}
    assistant = out[2]
    assert assistant["role"] == "assistant" and assistant["content"] == "on it"
    call = assistant["tool_calls"][0]
    assert call["id"] == "t1" and call["function"]["name"] == "write_file"
    assert json.loads(call["function"]["arguments"]) == {"path": "a.ts"}
    # The tool result becomes its own `role="tool"` message, keyed back to the call it answers.
    assert out[3] == {"role": "tool", "tool_call_id": "t1", "content": "ok"}


def test_assistant_message_with_only_tool_calls_has_null_content() -> None:
    convo: list[dict[str, Any]] = [
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "x", "name": "n", "input": {}}],
        }
    ]
    out = _to_openai_messages(None, convo)
    assert out[0]["content"] is None
    assert out[0]["tool_calls"][0]["id"] == "x"


# --------------------------------------------------------------------- stream reassembly


def _delta(content: Any = None, tool_calls: Any = None) -> SimpleNamespace:
    return SimpleNamespace(content=content, tool_calls=tool_calls)


def _choice(delta: SimpleNamespace, finish_reason: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(delta=delta, finish_reason=finish_reason)


def _tool_frag(
    index: int, *, call_id: str | None = None, name: str | None = None, args: str = ""
) -> Any:
    return SimpleNamespace(
        index=index, id=call_id, function=SimpleNamespace(name=name, arguments=args)
    )


async def test_accumulate_reassembles_text_a_fragmented_tool_call_and_usage() -> None:
    chunks = [
        SimpleNamespace(choices=[_choice(_delta(content="Hel"))], usage=None),
        SimpleNamespace(choices=[_choice(_delta(content="lo"))], usage=None),
        SimpleNamespace(
            choices=[
                _choice(
                    _delta(
                        tool_calls=[_tool_frag(0, call_id="call_1", name="write_file", args='{"pa')]
                    )
                )
            ],
            usage=None,
        ),
        SimpleNamespace(
            choices=[_choice(_delta(tool_calls=[_tool_frag(0, args='th":"a.ts"}')]), "tool_calls")],
            usage=None,
        ),
        # Trailing usage-only chunk (stream_options.include_usage), no choices.
        SimpleNamespace(choices=[], usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20)),
    ]

    events = [event async for event in _accumulate(_aiter(chunks))]

    assert [e.text for e in events if isinstance(e, TextDelta)] == ["Hel", "lo"]
    final = events[-1]
    assert isinstance(final, TurnComplete)
    assert final.text == "Hello"
    assert final.stop_reason == "tool_use"
    assert final.usage.input_tokens == 100 and final.usage.output_tokens == 20
    assert len(final.tool_uses) == 1
    call = final.tool_uses[0]
    assert call.id == "call_1" and call.name == "write_file"
    assert call.input == {"path": "a.ts"}  # fragments across two chunks parsed into one object


async def test_accumulate_plain_text_answer_has_no_tool_uses() -> None:
    chunks = [
        SimpleNamespace(choices=[_choice(_delta(content="done"), "stop")], usage=None),
        SimpleNamespace(choices=[], usage=SimpleNamespace(prompt_tokens=5, completion_tokens=2)),
    ]
    events = [event async for event in _accumulate(_aiter(chunks))]
    final = events[-1]
    assert isinstance(final, TurnComplete)
    assert final.text == "done" and final.tool_uses == []
    assert final.stop_reason == "end_turn"


def test_parse_tool_args_handles_empty_valid_and_rejects_malformed() -> None:
    assert _parse_tool_args("") == {}
    assert _parse_tool_args('{"a": 1}') == {"a": 1}
    with pytest.raises(ProviderError):
        _parse_tool_args("{not json")
    with pytest.raises(ProviderError):
        _parse_tool_args("[1, 2]")  # a JSON array is not a valid tool-argument object


# --------------------------------------------------------------------- provider selection


def test_default_transport_is_anthropic_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    reset_config()
    assert isinstance(default_transport(), SdkTransport)


def test_default_transport_selects_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    reset_config()
    assert isinstance(default_transport(), OpenAITransport)


def test_unknown_provider_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "bedrock")
    reset_config()
    with pytest.raises(SystemError):
        default_transport()


# --------------------------------------------------------------------- transient classification
#
# A compatible server (NVIDIA NIM, vLLM, …) can answer 200, open the SSE stream and only then report
# that it is out of capacity. The SDK raises a bare `APIError` with no status code, so the retry
# decision rests entirely on the message — these pin that classification.

httpx = pytest.importorskip("httpx")
openai_sdk = pytest.importorskip("openai")


def _request() -> Any:
    return httpx.Request("POST", "https://example.invalid/v1/chat/completions")


def _response(status: int) -> Any:
    return httpx.Response(status, request=_request())


def _mid_stream_error(message: str) -> Any:
    """The exact shape `openai._streaming` raises for an error inside a 200 SSE body."""
    return openai_sdk.APIError(message=message, request=_request(), body=None)


def test_mid_stream_capacity_error_is_transient() -> None:
    # The real NVIDIA message that surfaced as an unretried 500.
    exc = _mid_stream_error("ResourceExhausted: Worker local total request limit reached (16/16)")
    # No status code to classify by (HTTP was 200) — the message is the only signal there is.
    assert not hasattr(exc, "status_code")
    assert _is_transient_openai_error(exc) is True


@pytest.mark.parametrize(
    "message",
    [
        "Rate limit exceeded, please try again",
        "The server is busy right now",
        "Service Unavailable",
        "Model is overloaded",
    ],
)
def test_other_capacity_phrasings_are_transient(message: str) -> None:
    assert _is_transient_openai_error(_mid_stream_error(message)) is True


def test_mid_stream_business_error_is_not_retried() -> None:
    # A genuine rejection must fail fast rather than burn retries on a guaranteed failure.
    exc = _mid_stream_error("This model does not support tool use")
    assert _is_transient_openai_error(exc) is False


@pytest.mark.parametrize("status", [429, 500, 503, 408])
def test_retryable_status_codes(status: int) -> None:
    exc = openai_sdk.APIStatusError("busy", response=_response(status), body=None)
    assert _is_transient_openai_error(exc) is True


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_client_errors_are_not_retryable(status: int) -> None:
    exc = openai_sdk.APIStatusError("nope", response=_response(status), body=None)
    assert _is_transient_openai_error(exc) is False


def test_connection_errors_are_transient() -> None:
    assert _is_transient_openai_error(openai_sdk.APIConnectionError(request=_request())) is True


def test_capacity_message_is_actionable() -> None:
    exc = _mid_stream_error("ResourceExhausted: Worker local total request limit reached (16/16)")

    exhausted = _provider_message(exc, produced=False)
    assert "out of capacity" in exhausted
    assert "16/16" in exhausted  # the provider's own words survive, so the cause is visible

    # Having already streamed tokens, the turn cannot be replayed — say so instead of promising
    # a retry that never happened.
    assert "mid-response" in _provider_message(exc, produced=True)


def test_rejection_message_does_not_claim_capacity() -> None:
    assert "rejected the request" in _provider_message(
        _mid_stream_error("This model does not support tool use"), produced=False
    )


def test_backoff_grows_and_is_capped() -> None:
    assert _backoff_delay(0) < _backoff_delay(3)
    assert _backoff_delay(99) <= 20.25  # cap + max jitter


# --------------------------------------------------------------------- unconfigured credentials


async def test_missing_anthropic_key_is_a_provider_error_not_a_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unconfigured key must fail soft (502), never as an opaque 500.

    The SDK signals missing credentials with a bare ``TypeError`` from deep inside request building;
    letting that escape turns a fixable configuration problem into "Internal server error".
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    reset_config()

    request = MessageRequest(
        model="claude-sonnet-5", system=None, messages=[], tools=[], max_tokens=16
    )
    with pytest.raises(ProviderError, match="No Anthropic API key"):
        async for _ in SdkTransport().stream(request):
            pass
