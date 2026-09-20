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


#: What OpenRouter actually sent when its cheapest upstream stalled mid-generation. Verbatim,
#: because this classification rests entirely on the wording.
_UPSTREAM_TIMEOUT = (
    "Upstream error from InferenceNet: Inference request timed out "
    "(generation ID = gv2_j7l6KkISdzGaLPunQ3AVO)"
)


def test_a_brokered_upstream_timeout_is_transient() -> None:
    """A broker cannot fail over once the stream is open, so the retry has to be ours.

    Unmatched, this read as a rejection and burned none of the four retries — the one case where
    retrying is most likely to work, because it re-enters OpenRouter's routing and may well land
    on a different upstream.
    """
    exc = _mid_stream_error(_UPSTREAM_TIMEOUT)
    assert not hasattr(exc, "status_code")  # HTTP was 200; only the text can classify it
    assert _is_transient_openai_error(exc) is True


@pytest.mark.parametrize(
    "message",
    [
        "Inference request timed out",
        "Request timeout",
        "context deadline exceeded",
        "Gateway Timeout",
    ],
)
def test_other_timeout_phrasings_are_transient(message: str) -> None:
    assert _is_transient_openai_error(_mid_stream_error(message)) is True


def test_an_upstream_timeout_keeps_the_provider_wording() -> None:
    """The upstream's name and generation id are how a report is traced back to the broker's own
    logs — never swallow them."""
    message = _provider_message(_mid_stream_error(_UPSTREAM_TIMEOUT), produced=False)

    assert "rejected the request" not in message  # it was accepted, then it stalled
    assert "InferenceNet" in message
    assert "gv2_j7l6KkISdzGaLPunQ3AVO" in message


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


async def test_missing_openai_key_is_a_provider_error_not_a_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same guarantee on the OpenAI-compatible path.

    A key set from the admin panel is Fernet-encrypted; rotating ``FERNET_KEY`` makes it
    undecryptable and ``config_db`` then fails soft to the (blank) env value. Without this check
    the SDK's bare ``OpenAIError`` escaped as a 500, hiding the one thing worth saying: re-enter
    the key.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "")
    reset_config()

    request = MessageRequest(model="gpt-4o-mini", system=None, messages=[], tools=[], max_tokens=16)
    with pytest.raises(ProviderError, match="No OpenAI-compatible API key"):
        async for _ in OpenAITransport().stream(request):
            pass


# --------------------------------------------------------------------- reasoning (phase-64)


def _reasoning_delta(
    reasoning: str | None = None, details: list[dict[str, Any]] | None = None
) -> SimpleNamespace:
    return SimpleNamespace(
        content=None, tool_calls=None, reasoning=reasoning, reasoning_details=details
    )


async def test_accumulate_collects_reasoning_and_reasoning_tokens() -> None:
    """OpenRouter streams a reasoning model's thinking as `delta.reasoning` + `reasoning_details`
    fragments keyed by index, and reports the share of tokens spent thinking. All three used to be
    dropped on the floor — which is why an 8190-token think followed by nothing looked like an
    empty answer."""
    from app.agents.anthropic_client import ReasoningDelta

    chunks = [
        SimpleNamespace(
            choices=[
                _choice(
                    _reasoning_delta(
                        "Let me ", [{"type": "reasoning.text", "index": 0, "text": "Let me "}]
                    )
                )
            ],
            usage=None,
        ),
        SimpleNamespace(
            choices=[
                _choice(
                    _reasoning_delta(
                        "think.", [{"type": "reasoning.text", "index": 0, "text": "think."}]
                    )
                )
            ],
            usage=None,
        ),
        SimpleNamespace(
            choices=[
                _choice(
                    _delta(
                        tool_calls=[
                            _tool_frag(0, call_id="c1", name="list_dir", args='{"path":"."}')
                        ]
                    ),
                    "tool_calls",
                )
            ],
            usage=None,
        ),
        SimpleNamespace(
            choices=[],
            usage=SimpleNamespace(
                prompt_tokens=100,
                completion_tokens=40,
                completion_tokens_details=SimpleNamespace(reasoning_tokens=12),
            ),
        ),
    ]

    events = [event async for event in _accumulate(_aiter(chunks))]

    assert [e.text for e in events if isinstance(e, ReasoningDelta)] == ["Let me ", "think."]
    assert not [e for e in events if isinstance(e, TextDelta)]  # reasoning is never answer text
    final = events[-1]
    assert isinstance(final, TurnComplete)
    assert final.text == ""
    assert final.usage.reasoning_tokens == 12 and final.usage.output_tokens == 40
    assert final.reasoning == {
        "reasoning": "Let me think.",
        # fragments of one detail reassembled by index; string fields concatenated
        "reasoning_details": [{"type": "reasoning.text", "index": 0, "text": "Let me think."}],
    }
    assert [c.name for c in final.tool_uses] == ["list_dir"]


async def test_a_turn_cut_off_mid_tool_call_is_reported_as_truncated_not_as_a_fault() -> None:
    """`finish_reason=length` with a half-built argument JSON is the *expected* shape of a turn the
    cap interrupted. Dropping the call and letting the loop nudge beats failing the whole build."""
    chunks = [
        SimpleNamespace(
            choices=[
                _choice(
                    _delta(
                        tool_calls=[
                            _tool_frag(
                                0, call_id="c1", name="write_file", args='{"path":"a.ts","co'
                            )
                        ]
                    ),
                    "length",
                )
            ],
            usage=None,
        ),
    ]
    events = [event async for event in _accumulate(_aiter(chunks))]
    final = events[-1]
    assert isinstance(final, TurnComplete)
    assert final.stop_reason == "max_tokens" and final.truncated
    assert final.tool_uses == []


async def test_malformed_tool_args_on_a_finished_turn_are_still_a_fault() -> None:
    chunks = [
        SimpleNamespace(
            choices=[
                _choice(
                    _delta(tool_calls=[_tool_frag(0, call_id="c1", name="write_file", args="{no")]),
                    "tool_calls",
                )
            ],
            usage=None,
        ),
    ]
    with pytest.raises(ProviderError):
        [event async for event in _accumulate(_aiter(chunks))]


def test_assistant_turn_echoes_only_the_reasoning_the_endpoint_sent() -> None:
    from app.agents.anthropic_client import PROVIDER_REASONING_KEY, _assistant_message

    details = [{"type": "reasoning.text", "text": "…", "signature": "sig"}]
    turn = TurnComplete(
        text="ok",
        reasoning={"reasoning": "…", "reasoning_details": details},
        stop_reason="end_turn",
    )
    message = _assistant_message(turn)
    assert message[PROVIDER_REASONING_KEY] == {"reasoning": "…", "reasoning_details": details}

    out = _to_openai_messages(None, [message])
    assert out[0]["reasoning_details"] == details
    assert "reasoning" not in out[0]  # the plain-text mirror is derived; only details go back
    assert "reasoning_content" not in out[0]  # never sent → never echoed

    # Z.ai / DeepSeek shape: `reasoning_content` in, `reasoning_content` back.
    zai = _assistant_message(TurnComplete(text="ok", reasoning={"reasoning_content": "hmm"}))
    assert _to_openai_messages(None, [zai])[0]["reasoning_content"] == "hmm"

    # No reasoning received → a byte-identical assistant message, as before phase-64.
    plain = _assistant_message(TurnComplete(text="ok"))
    assert PROVIDER_REASONING_KEY not in plain
    assert _to_openai_messages(None, [plain])[0] == {"role": "assistant", "content": "ok"}


def test_the_anthropic_transport_strips_the_private_reasoning_key() -> None:
    from app.agents.anthropic_client import PROVIDER_REASONING_KEY, anthropic_messages

    convo: list[dict[str, Any]] = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "ok"}],
            PROVIDER_REASONING_KEY: {"reasoning_details": [{"text": "…"}]},
        },
    ]
    stripped = anthropic_messages(convo)
    assert stripped == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
    ]
    assert PROVIDER_REASONING_KEY in convo[1]  # the loop's transcript itself is untouched


def test_reasoning_params_shapes() -> None:
    from app.agents.anthropic_client import reasoning_params

    assert reasoning_params("", 0) == {}
    assert reasoning_params("low", 0) == {"effort": "low"}
    assert reasoning_params("HIGH", 0) == {"effort": "high"}
    assert reasoning_params("none", 0) == {"enabled": False}
    assert reasoning_params("", 2048) == {"max_tokens": 2048}
    assert reasoning_params("medium", 2048) == {"effort": "medium", "max_tokens": 2048}
    assert reasoning_params("extreme", 0) == {}  # a typo costs the hint, never the call


def test_build_openai_params_adds_the_reasoning_hint_only_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.agents.anthropic_client import build_openai_params

    request = MessageRequest(model="m", system=None, messages=[], tools=[], max_tokens=10)

    monkeypatch.delenv("LLM_REASONING_EFFORT", raising=False)
    monkeypatch.delenv("LLM_REASONING_MAX_TOKENS", raising=False)
    reset_config()
    unset = build_openai_params(request, endpoint="https://api.example.com/v1")
    assert "extra_body" not in unset  # byte-identical to before phase-64

    monkeypatch.setenv("LLM_REASONING_EFFORT", "low")
    monkeypatch.setenv("LLM_REASONING_MAX_TOKENS", "4096")
    reset_config()
    configured = build_openai_params(request, endpoint="https://openrouter.ai/api/v1")
    assert configured["extra_body"]["reasoning"] == {"effort": "low", "max_tokens": 4096}
    assert configured["extra_body"]["provider"] == {"sort": "price"}  # OpenRouter routing kept
