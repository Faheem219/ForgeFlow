"""The transport's repair loop (phase-57) — no network, no API key.

`OpenAITransport.run` takes the call as an argument precisely so this is testable: the loop's
behaviour (repair, retry, give up) is the part that has to be right, and it should not need a
provider to prove it.

The headline case is the reported bug end to end: a model that 400s on `max_tokens` completes
normally, because the transport reads the error, renames, and retries.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest

from app.agents.anthropic_client import (
    MessageRequest,
    OpenAITransport,
    StreamEvent,
    TextDelta,
    TurnComplete,
)
from app.agents.provider_compat import ParamMemo
from app.core.config import reset_config
from app.core.errors import ProviderError

openai_sdk = pytest.importorskip("openai", reason="the openai SDK is optional")


@pytest.fixture(autouse=True)
def _fresh_config(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # A model with no seed rule, so each test exercises the *reactive* half in isolation.
    monkeypatch.setenv("OPENAI_MAX_RETRIES", "2")
    monkeypatch.setenv("OPENAI_MAX_PARAM_REPAIRS", "4")
    monkeypatch.setenv("OPENAI_PARAM_LEARNING", "false")  # no Mongo in these tests
    monkeypatch.setenv("LLM_PARAM_QUIRKS", "")
    reset_config()
    yield
    reset_config()


def _request(model: str = "some-model") -> MessageRequest:
    return MessageRequest(
        model=model,
        system="be helpful",
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        max_tokens=4096,
    )


def _error(status: int, message: str, body: dict[str, Any] | None = None) -> Any:
    response = httpx.Response(status, request=httpx.Request("POST", "https://api.openai.com/v1"))
    return openai_sdk.APIStatusError(message, response=response, body=body)


_MAX_TOKENS_MESSAGE = (
    "Unsupported parameter: 'max_tokens' is not supported with this model. "
    "Use 'max_completion_tokens' instead."
)
MAX_TOKENS_400 = _error(
    400,
    _MAX_TOKENS_MESSAGE,
    body={
        "param": "max_tokens",
        "code": "unsupported_parameter",
        "message": _MAX_TOKENS_MESSAGE,
    },
)

#: A second *repairable* rejection, of a parameter we really do send (unlike `temperature`, which
#: the body never carries) — used to prove repairs and retries are counted separately.
STREAM_OPTIONS_422 = _error(
    422,
    'Error code: 422 - {"detail":[{"type":"extra_forbidden","loc":["body","stream_options"],'
    '"msg":"Extra inputs are not permitted"}]}',
)


class _Chunk:
    """One streamed SSE chunk, shaped like the SDK's."""

    def __init__(self, text: str = "", finish: str | None = None) -> None:
        self.usage = None
        self.choices = [
            type(
                "Choice",
                (),
                {
                    "delta": type("Delta", (), {"content": text, "tool_calls": None})(),
                    "finish_reason": finish,
                },
            )()
        ]


async def _ok_stream() -> AsyncIterator[Any]:
    yield _Chunk("hello")
    yield _Chunk("", finish="stop")


class FakeEndpoint:
    """Raises the scripted errors in order, then streams a normal response."""

    def __init__(self, *errors: Any) -> None:
        self._errors = list(errors)
        self.bodies: list[dict[str, Any]] = []

    async def create(self, params: dict[str, Any]) -> Any:
        self.bodies.append(dict(params))
        if self._errors:
            raise self._errors.pop(0)
        return _ok_stream()


async def _drain(
    transport: OpenAITransport, endpoint: FakeEndpoint, model: str = "some-model"
) -> list[StreamEvent]:
    events: list[StreamEvent] = []
    async for event in transport.run(
        _request(model),
        endpoint.create,
        endpoint="https://api.openai.com/v1",
        error_type=openai_sdk.OpenAIError,
    ):
        events.append(event)
    return events


#: A broker (OpenRouter) whose upstream stalled *after* the stream was open: HTTP 200, then an
#: error frame in the SSE body, which the SDK raises as a bare `APIError` with no status code.
UPSTREAM_TIMEOUT = openai_sdk.APIError(
    message=(
        "Upstream error from InferenceNet: Inference request timed out "
        "(generation ID = gv2_j7l6KkISdzGaLPunQ3AVO)"
    ),
    request=httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions"),
    body=None,
)


# ---------------------------------------------------------------- the reported bug, fixed


async def test_a_model_that_rejects_max_tokens_completes_after_one_repair() -> None:
    endpoint = FakeEndpoint(MAX_TOKENS_400)

    events = await _drain(OpenAITransport(ParamMemo()), endpoint)

    assert [e for e in events if isinstance(e, TextDelta)][0].text == "hello"
    assert isinstance(events[-1], TurnComplete)
    # Two calls: the rejected one, then the repaired one.
    assert len(endpoint.bodies) == 2
    assert "max_tokens" in endpoint.bodies[0]
    assert "max_tokens" not in endpoint.bodies[1]
    assert endpoint.bodies[1]["max_completion_tokens"] == 4096  # the cap survived the rename


async def test_a_repair_does_not_consume_a_transient_retry() -> None:
    """Repairs and retries are separate ladders: a repaired request is not a failed provider."""
    # Two repairs AND two transient failures, with OPENAI_MAX_RETRIES=2. If a repair consumed a
    # retry the budget would be exhausted before the last one and this would raise.
    endpoint = FakeEndpoint(
        MAX_TOKENS_400,
        STREAM_OPTIONS_422,
        _error(429, "Rate limit exceeded, please try again"),
        _error(500, "Internal server error"),
    )

    events = await _drain(OpenAITransport(ParamMemo()), endpoint)

    assert isinstance(events[-1], TurnComplete)
    assert len(endpoint.bodies) == 5  # 2 repaired + 2 retried + the one that succeeded
    assert "max_tokens" not in endpoint.bodies[-1]
    assert "stream_options" not in endpoint.bodies[-1]


async def test_the_learned_repair_is_applied_up_front_on_the_next_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cost of discovery is paid once, not on every call.

    Learning is enabled here with no Mongo running: `persist_quirks` fails and swallows it, which
    is the documented fail-soft path — the in-process memo still carries the repair, so the second
    call is correct regardless of whether the write landed.
    """
    monkeypatch.setenv("OPENAI_PARAM_LEARNING", "true")
    reset_config()
    memo = ParamMemo()

    first = FakeEndpoint(MAX_TOKENS_400)
    await _drain(OpenAITransport(memo), first)
    assert len(first.bodies) == 2  # rejected, then repaired

    assert memo.get("https://api.openai.com/v1", "some-model")  # it was remembered

    second = FakeEndpoint()  # no errors scripted: the first body must already be correct
    await _drain(OpenAITransport(memo), second)

    assert len(second.bodies) == 1
    assert "max_tokens" not in second.bodies[0]
    assert second.bodies[0]["max_completion_tokens"] == 4096


# ---------------------------------------------------------------- the bounds


async def test_the_same_rejection_twice_stops_instead_of_looping() -> None:
    """A repair that does not take means the loop is going nowhere; it must not be unbounded."""
    endpoint = FakeEndpoint(MAX_TOKENS_400, MAX_TOKENS_400, MAX_TOKENS_400)

    with pytest.raises(ProviderError) as caught:
        await _drain(OpenAITransport(ParamMemo()), endpoint)

    assert "max_tokens" in str(caught.value)
    assert len(endpoint.bodies) == 2  # the rejected call and the one repaired attempt


async def test_the_repair_budget_is_respected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_MAX_PARAM_REPAIRS", "0")
    reset_config()
    endpoint = FakeEndpoint(MAX_TOKENS_400)

    with pytest.raises(ProviderError):
        await _drain(OpenAITransport(ParamMemo()), endpoint)

    assert len(endpoint.bodies) == 1  # not repaired at all


async def test_an_unrepairable_400_still_fails_fast_with_no_retry() -> None:
    """The safety property, at the transport level."""
    endpoint = FakeEndpoint(_error(400, "Invalid API key provided"))

    with pytest.raises(ProviderError):
        await _drain(OpenAITransport(ParamMemo()), endpoint)

    assert len(endpoint.bodies) == 1


async def test_a_failure_after_tokens_were_produced_is_never_repaired() -> None:
    """Restarting a turn whose output already reached the UI would duplicate streamed text."""

    class MidStream(FakeEndpoint):
        async def create(self, params: dict[str, Any]) -> Any:
            self.bodies.append(dict(params))

            async def gen() -> AsyncIterator[Any]:
                yield _Chunk("partial")
                raise MAX_TOKENS_400

            return gen()

    endpoint = MidStream()

    with pytest.raises(ProviderError):
        await _drain(OpenAITransport(ParamMemo()), endpoint)

    assert len(endpoint.bodies) == 1  # no repair, no retry


# ---------------------------------------------------------------- the seed table on the wire


async def test_a_seeded_model_sends_the_right_body_on_the_very_first_call() -> None:
    """The point of the seed half: no wasted round-trip for a family we already know about."""
    endpoint = FakeEndpoint()

    await _drain(OpenAITransport(ParamMemo()), endpoint, model="gpt-5.4-mini")

    assert len(endpoint.bodies) == 1
    assert "max_tokens" not in endpoint.bodies[0]
    assert endpoint.bodies[0]["max_completion_tokens"] == 4096
    assert "temperature" not in endpoint.bodies[0]


async def test_a_brokered_upstream_timeout_is_retried_rather_than_surfaced() -> None:
    """The stall costs a retry, not the stage.

    `allow_fallbacks` cannot save a request whose stream is already open, so the second chance has
    to be ours — and because it re-enters the broker's routing, it may land on a different upstream
    entirely. The point of the assertion on the bodies: this is a *retry* (same request, sent
    again), not a param repair — nothing about the request was wrong.
    """
    endpoint = FakeEndpoint(UPSTREAM_TIMEOUT)

    events: list[StreamEvent] = []
    async for event in OpenAITransport(ParamMemo()).run(
        _request("z-ai/glm-5.3-flash"),
        endpoint.create,
        endpoint="https://openrouter.ai/api/v1",
        error_type=openai_sdk.OpenAIError,
    ):
        events.append(event)

    assert isinstance(events[-1], TurnComplete)  # the stage completed
    assert len(endpoint.bodies) == 2
    assert endpoint.bodies[0] == endpoint.bodies[1]  # resent unchanged, not repaired
    # The cheapest-upstream preference survives the retry — a retry must not silently cost more.
    assert endpoint.bodies[1]["extra_body"]["provider"] == {"sort": "price"}


async def test_an_upstream_timeout_that_keeps_timing_out_still_gives_up() -> None:
    """Bounded, like every other ladder here: three attempts at OPENAI_MAX_RETRIES=2, then stop."""
    endpoint = FakeEndpoint(UPSTREAM_TIMEOUT, UPSTREAM_TIMEOUT, UPSTREAM_TIMEOUT)

    with pytest.raises(ProviderError) as excinfo:
        async for _ in OpenAITransport(ParamMemo()).run(
            _request("z-ai/glm-5.3-flash"),
            endpoint.create,
            endpoint="https://openrouter.ai/api/v1",
            error_type=openai_sdk.OpenAIError,
        ):
            pass

    assert len(endpoint.bodies) == 3
    assert "InferenceNet" in str(excinfo.value)  # the upstream that failed is still named
