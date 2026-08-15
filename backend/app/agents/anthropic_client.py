"""Agent client (phase-20; multi-provider since phase-53): async streaming and a bounded tool-use
loop, with per-call cost accounting and budget guardrails baked in.

The transport (the thing that actually talks to a model provider) sits behind a :class:`Transport`
Protocol so the whole loop is testable against a scripted fake. Two concrete transports ship, both
lazy-imported (their SDKs are only needed for real calls) and never touched by the loop tests:

* :class:`SdkTransport` — Anthropic Messages API (the default).
* :class:`OpenAITransport` — any OpenAI-*compatible* Chat Completions endpoint (OpenAI, Azure,
  Together, Groq, OpenRouter, vLLM, Ollama, …), selected with ``LLM_PROVIDER=openai``.

The loop's internal message vocabulary is Anthropic-native (content blocks, ``tool_use`` /
``tool_result``); :class:`OpenAITransport` is a pure adapter at the edge that translates that shape
to and from Chat Completions, so no other module changes when the provider does.

Flow per turn: enforce budget → stream the turn (text deltas → ``agent.token`` events) → record cost
on the ``Run`` → enforce budget again → if the model called tools, dispatch them and feed results
back, else return. The loop is bounded by ``anthropic_max_tool_turns``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from beanie import PydanticObjectId

from app.agents.budget import enforce_budget
from app.agents.cost import Usage, record_cost
from app.agents.models import TaskKind, route
from app.core.config import get_config
from app.core.errors import ProviderError, SystemError, UserError  # noqa: A004 - taxonomy name
from app.db.models import Run
from app.realtime.hub import emit
from app.realtime.schemas import EventType

logger = logging.getLogger(__name__)

Emitter = Callable[..., Awaitable[Any]]
# A tool dispatcher: (tool_name, tool_input) -> result string fed back to the model.
ToolDispatch = Callable[[str, dict[str, Any]], Awaitable[str]]


# --------------------------------------------------------------------- transport contract


@dataclass
class MessageRequest:
    model: str
    system: str | None
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]]
    max_tokens: int


@dataclass
class TextDelta:
    """A streamed chunk of assistant text."""

    text: str


@dataclass
class ToolUse:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class TurnComplete:
    """The end of one assistant turn: its full text, any tool calls, usage, and stop reason."""

    text: str = ""
    tool_uses: list[ToolUse] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    stop_reason: str = "end_turn"


StreamEvent = TextDelta | TurnComplete


class Transport(Protocol):
    """A message transport: yields text deltas as they stream, then one :class:`TurnComplete`."""

    def stream(self, request: MessageRequest) -> AsyncIterator[StreamEvent]: ...


# --------------------------------------------------------------------- result


#: Why a tool loop ended before the model said it was done.
STOP_MAX_TURNS = "max_turns"  # the turn bound was reached
STOP_NO_PROGRESS = "no_progress"  # the same tool call, repeated, going nowhere

#: How many *identical* consecutive tool calls count as "stuck". Small on purpose: a model that
#: re-reads one file three times in a row is not making progress, and each turn costs tokens.
REPEAT_LIMIT = 3


@dataclass
class LoopResult:
    text: str
    messages: list[dict[str, Any]]
    usage: Usage
    turns: int
    #: ``None`` when the model finished on its own, else :data:`STOP_MAX_TURNS` /
    #: :data:`STOP_NO_PROGRESS`. Callers that report to the user must say so — the work done up to
    #: that point is real and already persisted, it is just not complete.
    stopped: str | None = None

    @property
    def finished(self) -> bool:
        return self.stopped is None


# --------------------------------------------------------------------- client


class AnthropicClient:
    def __init__(
        self,
        transport: Transport | None = None,
        *,
        emitter: Emitter = emit,
    ) -> None:
        # Keep an injected transport as-is (tests inject a fake). Otherwise resolve the provider
        # transport **lazily, per turn** (see :meth:`_stream_turn`) — never here. This object is
        # constructed at import time via the stage-handler registry, which runs *before* the admin
        # (DB) config layer is installed in the app lifespan; resolving now would freeze the
        # provider to the env default (`anthropic`) and silently ignore an LLM_PROVIDER set from the
        # admin panel. Deferring the choice also lets an admin switch providers with no restart.
        self._transport = transport
        self._emit = emitter

    def _config_int(self, key: str) -> int:
        return int(get_config().get(key))

    async def run_tool_loop(
        self,
        *,
        task_kind: TaskKind,
        project_id: PydanticObjectId,
        run: Run,
        messages: list[dict[str, Any]],
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_dispatch: ToolDispatch | None = None,
        channel: str | None = None,
        max_turns: int | None = None,
    ) -> LoopResult:
        """Drive a bounded tool-use loop and return the final assistant text + full transcript.

        ``max_turns`` overrides the ``anthropic_max_tool_turns`` config default for this one loop —
        so a caller that runs the loop N times (phase-56's per-phase codegen) can bound each phase
        instead of granting N × the global ceiling.
        """
        model = route(task_kind)
        max_tokens = self._config_int("anthropic_max_output_tokens")
        if max_turns is None:
            max_turns = self._config_int("anthropic_max_tool_turns")
        channel = channel or str(project_id)

        convo = [dict(m) for m in messages]
        total = Usage()
        last_text = ""
        repeated: tuple[str, str] | None = None
        repeats = 0

        for turn_index in range(max_turns):
            await enforce_budget(project_id, emitter=self._emit)  # never spend past the cap

            request = MessageRequest(
                model=model,
                system=system,
                messages=convo,
                tools=tools or [],
                max_tokens=max_tokens,
            )
            turn = await self._stream_turn(request, channel)

            await record_cost(run, model, turn.usage)
            total = total.plus(turn.usage)
            await enforce_budget(
                project_id, emitter=self._emit
            )  # halt if this call crossed the cap

            convo.append(_assistant_message(turn))
            last_text = turn.text or last_text

            if not turn.tool_uses:
                return LoopResult(text=turn.text, messages=convo, usage=total, turns=turn_index + 1)

            if tool_dispatch is None:
                raise UserError("The model requested a tool but no tool dispatcher was provided")

            # No-progress detection (D7's discipline, applied to the codegen loop too): a model that
            # issues the *same* call over and over will keep doing it until the turn bound, spending
            # real money for nothing. Stop and report instead.
            signature = _call_signature(turn)
            repeats = repeats + 1 if signature == repeated else 1
            repeated = signature
            if repeats >= REPEAT_LIMIT:
                logger.warning(
                    "tool loop stopped: %r repeated %d turns running", signature[0], repeats
                )
                return LoopResult(
                    text=last_text,
                    messages=convo,
                    usage=total,
                    turns=turn_index + 1,
                    stopped=STOP_NO_PROGRESS,
                )

            results: list[dict[str, Any]] = []
            for call in turn.tool_uses:
                output = await tool_dispatch(call.name, call.input)
                results.append({"type": "tool_result", "tool_use_id": call.id, "content": output})
            convo.append({"role": "user", "content": results})

        # The bound is a safety rail, not a failure of the *work*: everything the model did is
        # already persisted (files written, commits made). Raising here used to discard a whole
        # build report — the user saw a failed stage and no record of what got built. Report the
        # stop instead and let the caller present it as "unfinished, run again to resume".
        logger.warning("tool loop hit its %d-turn bound without finishing", max_turns)
        return LoopResult(
            text=last_text, messages=convo, usage=total, turns=max_turns, stopped=STOP_MAX_TURNS
        )

    async def _stream_turn(self, request: MessageRequest, channel: str) -> TurnComplete:
        """Stream one model turn, bracketed by explicit lifecycle events.

        The bracket exists because ``agent.token`` on its own is not a protocol: a client receiving
        deltas has no way to distinguish "the model paused" from "the turn is over", so any UI state
        keyed to token arrival stays on forever. ``agent.stream.end`` is emitted from a ``finally``
        so a transport error, a cancelled task, or a stream that dies mid-turn all still close the
        bracket — an error path that leaves the client believing it is still streaming is exactly
        the failure this is meant to remove.
        """
        # Resolve the provider transport at call time (unless one was injected), so the current
        # LLM_PROVIDER / provider credentials are honored even though this client may have been
        # built before the admin config layer loaded. Transports are cheap and stateless (each
        # builds its SDK client inside .stream()), so per-turn resolution costs nothing.
        transport = self._transport if self._transport is not None else default_transport()
        turn: TurnComplete | None = None
        await self._emit(channel, EventType.agent_stream_start, {"model": request.model})
        try:
            async for event in transport.stream(request):
                if isinstance(event, TextDelta):
                    if event.text:
                        await self._emit(channel, EventType.agent_token, {"text": event.text})
                else:
                    turn = event
        finally:
            await self._emit(
                channel,
                EventType.agent_stream_end,
                {"model": request.model, "completed": turn is not None},
            )
        if turn is None:
            raise ProviderError("The model stream ended without a completion")
        return turn


def _call_signature(turn: TurnComplete) -> tuple[str, str]:
    """A comparable fingerprint of what this turn asked for: (tool names, serialized arguments).

    Used only to notice a loop repeating itself verbatim, so it must be stable but need not be
    pretty; unserializable arguments fall back to ``repr``.
    """
    names = ",".join(call.name for call in turn.tool_uses)
    try:
        args = json.dumps([call.input for call in turn.tool_uses], sort_keys=True, default=str)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        args = repr([call.input for call in turn.tool_uses])
    return names, args


def _assistant_message(turn: TurnComplete) -> dict[str, Any]:
    """Rebuild the assistant turn as Anthropic content blocks for the next request."""
    content: list[dict[str, Any]] = []
    if turn.text:
        content.append({"type": "text", "text": turn.text})
    for call in turn.tool_uses:
        content.append({"type": "tool_use", "id": call.id, "name": call.name, "input": call.input})
    return {"role": "assistant", "content": content}


# --------------------------------------------------------------------- real SDK transport


def _import_anthropic() -> Any:  # pragma: no cover - only exercised with real creds/SDK installed
    try:
        import anthropic
    except ModuleNotFoundError as exc:
        raise ProviderError(
            "The 'anthropic' package is not installed; cannot reach Claude"
        ) from exc
    return anthropic


class SdkTransport:
    """Default transport over the ``anthropic`` async SDK. Lazy-imported; retries transient errors.

    Not exercised by the test suite (no SDK/creds there) — the loop is tested via a fake transport.
    """

    async def stream(  # pragma: no cover - real network path
        self, request: MessageRequest
    ) -> AsyncIterator[StreamEvent]:
        anthropic = _import_anthropic()
        config = get_config()
        base_url = str(config.get("anthropic_base_url")) or None
        api_key = str(config.get("anthropic_api_key")).strip()
        # Check the key here rather than letting the SDK raise. Unconfigured credentials are a
        # *provider* problem (502, actionable) — the SDK signals them with a bare ``TypeError``,
        # which would otherwise escape as an opaque 500 "Internal server error".
        if not api_key:
            raise ProviderError(
                "No Anthropic API key is configured. Set it in the admin panel under Models, or "
                "switch LLM_PROVIDER to an OpenAI-compatible endpoint.",
                fallback_hint="configure_provider",
            )
        client = anthropic.AsyncAnthropic(api_key=api_key, base_url=base_url)

        kwargs: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "messages": request.messages,
        }
        if request.system:
            kwargs["system"] = request.system
        if request.tools:
            kwargs["tools"] = request.tools

        final = None
        async with await self._with_retries(lambda: client.messages.stream(**kwargs)) as stream:
            async for text in stream.text_stream:
                yield TextDelta(text)
            final = await stream.get_final_message()

        tool_uses = [
            ToolUse(id=block.id, name=block.name, input=dict(block.input))
            for block in final.content
            if getattr(block, "type", None) == "tool_use"
        ]
        text = "".join(
            getattr(block, "text", "")
            for block in final.content
            if getattr(block, "type", None) == "text"
        )
        yield TurnComplete(
            text=text,
            tool_uses=tool_uses,
            usage=Usage(final.usage.input_tokens, final.usage.output_tokens),
            stop_reason=final.stop_reason or "end_turn",
        )

    async def _with_retries(self, make_call: Callable[[], Any]) -> Any:  # pragma: no cover
        anthropic = _import_anthropic()
        retryable = (
            anthropic.APIConnectionError,
            anthropic.RateLimitError,
            anthropic.InternalServerError,
        )
        attempts = int(get_config().get("anthropic_max_retries"))
        for attempt in range(attempts + 1):
            try:
                return make_call()
            except retryable as exc:
                if attempt >= attempts:
                    raise ProviderError("Anthropic request failed after retries") from exc
                await asyncio.sleep(_backoff_delay(attempt))
        raise ProviderError("Anthropic request failed")  # unreachable


# --------------------------------------------------------------------- OpenAI-compatible transport


def _import_openai() -> Any:  # pragma: no cover - only exercised with the SDK installed
    try:
        import openai
    except ModuleNotFoundError as exc:
        raise ProviderError(
            "The 'openai' package is not installed; run `uv sync --extra openai` "
            "(or `pip install openai`) to use LLM_PROVIDER=openai"
        ) from exc
    return openai


#: OpenAI ``finish_reason`` → the internal (Anthropic-style) stop reason. Informational: the loop
#: decides whether to continue from the presence of tool calls, not this string.
_STOP_REASONS = {
    "tool_calls": "tool_use",
    "stop": "end_turn",
    "length": "max_tokens",
    "content_filter": "content_filter",
}


def _to_openai_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Anthropic tool schema (``name``/``description``/``input_schema``) → OpenAI function tools."""
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema") or {"type": "object", "properties": {}},
            },
        }
        for tool in tools
    ]


def _tool_result_text(content: Any) -> str:
    """A ``tool_result`` payload as a plain string (Chat Completions tool messages are text)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # Anthropic allows a list of blocks; keep the text parts
        return "".join(
            block.get("text", "") for block in content if isinstance(block, dict)
        ) or json.dumps(content)
    return json.dumps(content)


def _to_openai_messages(system: str | None, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate the loop's Anthropic-shaped transcript into OpenAI Chat Completions messages.

    ``system`` becomes a leading system message; assistant ``tool_use`` blocks become
    ``tool_calls``; user ``tool_result`` blocks become ``role="tool"`` messages (emitted in order,
    so each follows the assistant turn that requested it, as Chat Completions requires).
    """
    out: list[dict[str, Any]] = []
    if system:
        out.append({"role": "system", "content": system})

    for message in messages:
        role = message.get("role")
        content = message.get("content")

        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue

        blocks = content or []
        if role == "assistant":
            text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
            tool_calls = [
                {
                    "id": b["id"],
                    "type": "function",
                    "function": {"name": b["name"], "arguments": json.dumps(b.get("input") or {})},
                }
                for b in blocks
                if b.get("type") == "tool_use"
            ]
            assistant: dict[str, Any] = {"role": "assistant", "content": text or None}
            if tool_calls:
                assistant["tool_calls"] = tool_calls
            out.append(assistant)
        else:  # user turn: tool results (each its own message) and/or plain text
            text_parts: list[str] = []
            for b in blocks:
                if b.get("type") == "tool_result":
                    out.append(
                        {
                            "role": "tool",
                            "tool_call_id": b.get("tool_use_id"),
                            "content": _tool_result_text(b.get("content")),
                        }
                    )
                elif b.get("type") == "text":
                    text_parts.append(b.get("text", ""))
            if text_parts:
                out.append({"role": "user", "content": "".join(text_parts)})
    return out


def _parse_tool_args(raw: str) -> dict[str, Any]:
    """The accumulated tool-call argument JSON as a dict. Empty → ``{}``; malformed is a fault."""
    raw = raw.strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProviderError(f"Model returned invalid tool-call arguments: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ProviderError("Model tool-call arguments were not a JSON object")
    return parsed


async def _accumulate(chunks: AsyncIterator[Any]) -> AsyncIterator[StreamEvent]:
    """Fold an OpenAI streaming response into text deltas + one :class:`TurnComplete`.

    Chat Completions streams tool calls in fragments keyed by ``index`` (id/name arrive once, the
    argument JSON dribbles in across chunks), and reports usage on a trailing choice-less chunk when
    ``stream_options.include_usage`` is set — both are reassembled here. Kept as a module function,
    independent of the SDK, so the reassembly is unit-tested with scripted chunks.
    """
    text_parts: list[str] = []
    frags: dict[int, dict[str, str]] = {}
    usage = Usage()
    stop_reason = "end_turn"

    async for chunk in chunks:
        reported = getattr(chunk, "usage", None)
        if reported is not None:
            usage = Usage(
                int(getattr(reported, "prompt_tokens", 0) or 0),
                int(getattr(reported, "completion_tokens", 0) or 0),
            )
        choices = getattr(chunk, "choices", None) or []
        if not choices:
            continue
        choice = choices[0]

        delta = getattr(choice, "delta", None)
        if delta is not None:
            piece = getattr(delta, "content", None)
            if piece:
                text_parts.append(piece)
                yield TextDelta(piece)
            for call in getattr(delta, "tool_calls", None) or []:
                index = int(getattr(call, "index", 0) or 0)
                frag = frags.setdefault(index, {"id": "", "name": "", "args": ""})
                if getattr(call, "id", None):
                    frag["id"] = call.id
                fn = getattr(call, "function", None)
                if fn is not None:
                    if getattr(fn, "name", None):
                        frag["name"] = fn.name
                    if getattr(fn, "arguments", None):
                        frag["args"] += fn.arguments

        finish = getattr(choice, "finish_reason", None)
        if finish:
            stop_reason = _STOP_REASONS.get(finish, finish)

    tool_uses = [
        ToolUse(
            id=frags[i]["id"] or f"call_{i}",
            name=frags[i]["name"],
            input=_parse_tool_args(frags[i]["args"]),
        )
        for i in sorted(frags)
    ]
    yield TurnComplete(
        text="".join(text_parts), tool_uses=tool_uses, usage=usage, stop_reason=stop_reason
    )


#: Substrings marking a **mid-stream** provider failure as transient. An OpenAI-compatible server
#: (NVIDIA NIM, vLLM, Together, …) may accept a request (HTTP 200), open the SSE stream, and only
#: then report that it is out of capacity — e.g. "ResourceExhausted: Worker local total request
#: limit reached (16/16)". The SDK surfaces that as a bare ``APIError`` carrying **no status code**
#: (the response really was a 200), so its text is the only signal available to classify it.
_TRANSIENT_STREAM_MARKERS: tuple[str, ...] = (
    "resourceexhausted",
    "resource_exhausted",
    "request limit reached",
    "rate limit",
    "too many requests",
    "overloaded",
    "server is busy",
    "temporarily unavailable",
    "service unavailable",
    "try again",
)


def _backoff_delay(attempt: int) -> float:
    """Capped exponential backoff with jitter, so retries spread out instead of stampeding."""
    return float(min(20.0, 0.5 * 2**attempt)) + random.uniform(0, 0.25)


def _is_transient_openai_error(exc: BaseException) -> bool:
    """Whether an ``openai`` SDK error is worth another attempt.

    Errors that carry an HTTP status are classified by that status; a mid-stream ``APIError`` (which
    has none — see :data:`_TRANSIENT_STREAM_MARKERS`) is classified by its message instead.
    """
    openai = _import_openai()
    if isinstance(exc, openai.APIConnectionError):  # network blip / timeout
        return True
    if isinstance(exc, openai.APIStatusError):
        status = int(getattr(exc, "status_code", 0) or 0)
        return status in (408, 409, 425, 429) or status >= 500
    if isinstance(exc, openai.APIError):
        return any(marker in str(exc).lower() for marker in _TRANSIENT_STREAM_MARKERS)
    return False


def _provider_message(exc: BaseException, *, produced: bool) -> str:
    """A user-facing explanation for a failed provider call (rendered by the API as a 502)."""
    text = str(exc).strip() or exc.__class__.__name__
    if not _is_transient_openai_error(exc):
        return f"The model provider rejected the request ({text})."
    if produced:
        return (
            f"The model provider ran out of capacity mid-response ({text}). The partial response "
            "was discarded — run the stage again."
        )
    return (
        f"The model provider is out of capacity ({text}). Retries were exhausted — try again "
        "shortly, or point MODEL_CODEGEN / MODEL_ROUTING at a less contended model."
    )


#: Endpoints (by base URL) that answered "unknown field" to ``stream_options``. Mistral's API and
#: Azure's serverless deployments validate the body strictly and reject anything outside their
#: schema with a 422, so the usage trailer is dropped for them (:func:`_rejects_stream_options`).
_NO_STREAM_USAGE: set[str] = set()


def build_openai_params(request: MessageRequest, *, stream_usage: bool = True) -> dict[str, Any]:
    """Translate the loop's Anthropic-shaped request into Chat Completions params.

    Pure, so the wire format is testable without a network or an SDK. ``stream_usage`` asks for the
    token-usage trailer; it is dropped for endpoints that reject unknown fields (cost accounting
    then reads zero tokens for that provider, which beats every call failing).
    """
    params: dict[str, Any] = {
        "model": request.model,
        "max_tokens": request.max_tokens,
        "messages": _to_openai_messages(request.system, request.messages),
        "stream": True,
    }
    if stream_usage:
        params["stream_options"] = {"include_usage": True}
    if request.tools:
        params["tools"] = _to_openai_tools(request.tools)
    return params


def _rejects_stream_options(exc: BaseException) -> bool:
    """Whether this failure is the endpoint refusing ``stream_options`` as an unknown field.

    Mistral answers ``422 extra_forbidden`` / "Extra inputs are not permitted" (its schema has no
    such field), and Azure's serverless endpoints do the same. That is a *request* problem we can
    fix ourselves rather than an outage, so it is handled separately from the transient retries.
    """
    text = str(exc).lower()
    if "stream_options" not in text and "include_usage" not in text:
        return False
    markers = (
        "extra_forbidden",
        "extra inputs are not permitted",
        "not permitted",
        "unknown",
        "unrecognized",
        "unsupported",
        "not supported",
        "invalid",
    )
    if any(marker in text for marker in markers):
        return True
    status = getattr(exc, "status_code", None)
    return status in (400, 422)


class OpenAITransport:
    """Transport over any OpenAI-compatible Chat Completions endpoint (phase-53).

    A pure adapter: it translates the loop's Anthropic-shaped request into Chat Completions params,
    streams, and folds the response back into the loop's :class:`StreamEvent`s. Like
    :class:`SdkTransport`, the real network path is lazy-imported and not exercised by tests; the
    translation, stream-reassembly and retry-classification helpers it delegates to are.
    """

    async def stream(  # pragma: no cover - real network path
        self, request: MessageRequest
    ) -> AsyncIterator[StreamEvent]:
        openai = _import_openai()
        config = get_config()
        base_url = str(config.get("openai_base_url")) or None
        api_key = str(config.get("openai_api_key")).strip()
        # Same explicit check as SdkTransport's: unconfigured (or undecryptable — see
        # config_db._decrypt) credentials are a *provider* problem (502, actionable), not an opaque
        # 500 from the SDK's bare OpenAIError.
        if not api_key:
            raise ProviderError(
                "No OpenAI-compatible API key is configured. Set it in the admin panel under "
                "Models, or switch LLM_PROVIDER to Anthropic.",
                fallback_hint="configure_provider",
            )
        client = openai.AsyncOpenAI(api_key=api_key, base_url=base_url)

        endpoint = base_url or "default"
        params = build_openai_params(request, stream_usage=endpoint not in _NO_STREAM_USAGE)

        # The retry spans **opening and consuming** the stream, not just opening it: a compatible
        # server can answer 200 and only then fail inside the SSE body, which is precisely the case
        # a create-only retry would miss.
        attempts = int(config.get("openai_max_retries"))
        for attempt in range(attempts + 1):
            produced = False
            try:
                raw = await client.chat.completions.create(**params)
                async for event in _accumulate(raw):
                    produced = True
                    yield event
                return
            except openai.OpenAIError as exc:
                # A strict endpoint (Mistral, Azure serverless) refusing the usage trailer is a
                # request we can repair, not an outage: drop it, remember this endpoint refuses it,
                # and try again. Without this every call to such a provider fails with a 422.
                if not produced and "stream_options" in params and _rejects_stream_options(exc):
                    _NO_STREAM_USAGE.add(endpoint)
                    params.pop("stream_options", None)
                    logger.info(
                        "%s rejects stream_options; retrying without token-usage reporting",
                        endpoint,
                    )
                    continue
                # Once tokens have been emitted the turn cannot be restarted — replaying it would
                # duplicate streamed output in the UI — so only a pre-output failure is retryable.
                if produced or attempt >= attempts or not _is_transient_openai_error(exc):
                    raise ProviderError(_provider_message(exc, produced=produced)) from exc
                delay = _backoff_delay(attempt)
                # A silent retry chain looks identical to a hung request from the outside; say so.
                logger.warning(
                    "model provider unavailable, retrying in %.1fs (attempt %d/%d): %s",
                    delay,
                    attempt + 1,
                    attempts,
                    exc,
                )
                await asyncio.sleep(delay)
        raise ProviderError("The model provider request failed")  # unreachable


# --------------------------------------------------------------------- provider selection


def default_transport() -> Transport:
    """The transport for the configured provider (``LLM_PROVIDER``). Anthropic is the default."""
    provider = str(get_config().get("llm_provider")).strip().lower()
    if provider in ("", "anthropic"):
        return SdkTransport()
    if provider == "openai":
        return OpenAITransport()
    raise SystemError(f"Unknown LLM_PROVIDER {provider!r}; expected 'anthropic' or 'openai'")


#: The agent client is provider-agnostic; the historical name is kept for its many call sites.
AgentClient = AnthropicClient
