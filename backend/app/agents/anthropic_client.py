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
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlsplit

from beanie import PydanticObjectId

from app.agents.budget import enforce_budget
from app.agents.cost import Usage, record_cost
from app.agents.models import TaskKind, route
from app.agents.provider_compat import (
    ParamMemo,
    Repair,
    apply_repairs,
    diagnose_param_error,
    parse_quirks,
    persist_quirks,
    seed_repairs,
)
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
class ReasoningDelta:
    """A streamed chunk of the model's *reasoning* (phase-64).

    Reasoning models think before they answer, and OpenAI-compatible endpoints stream that thinking
    as its own delta. It is not assistant text — it must never be fed back as such or shown as the
    answer — but a two-minute burst of it looks exactly like a hang unless something is emitted.
    """

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
    #: phase-64: the provider's reasoning payload **as received** (``{"reasoning_details": [...]}``
    #: from OpenRouter, ``{"reasoning_content": "…"}`` from Z.ai/DeepSeek-style endpoints, …).
    #: Opaque to the loop; echoed back on the next turn so the model need not re-think from scratch.
    reasoning: dict[str, Any] | None = None

    @property
    def truncated(self) -> bool:
        """The provider cut this turn off at the output cap (``max_tokens`` / ``length``)."""
        return self.stop_reason == "max_tokens"


StreamEvent = TextDelta | ReasoningDelta | TurnComplete


class Transport(Protocol):
    """A message transport: yields text deltas as they stream, then one :class:`TurnComplete`."""

    def stream(self, request: MessageRequest) -> AsyncIterator[StreamEvent]: ...


# --------------------------------------------------------------------- result


#: Why a tool loop ended before the model said it was done.
STOP_MAX_TURNS = "max_turns"  # the turn bound was reached
STOP_NO_PROGRESS = "no_progress"  # the same tool call, repeated, going nowhere
STOP_MAX_TOKENS = "max_tokens"  # the model kept running out of output budget (phase-64)

#: Reasoning deltas are batched into `agent.reasoning` events of about this many characters.
_REASONING_EMIT_CHARS = 400

#: How many *identical* consecutive tool calls count as "stuck". Small on purpose: a model that
#: re-reads one file three times in a row is not making progress, and each turn costs tokens.
REPEAT_LIMIT = 3

#: The corrective turn appended after a model turn that produced nothing usable (phase-64): cut
#: off at the output cap — typically a reasoning model that thought past ``max_tokens`` — or an
#: empty response. Prompt surface: bump ``PROMPT_VERSION`` when it changes.
NUDGE_TRUNCATED = (
    "Your previous turn ran out of output budget before producing a tool call (or produced "
    "nothing). Do not re-derive the plan — reason briefly, then act: call the tool now. Write "
    "large files in parts across several `write_file` calls."
)

#: The private key under which an assistant message carries the provider's reasoning payload
#: (phase-64). It sits BESIDE ``content`` (which stays Anthropic-shaped), is consumed only by the
#: OpenAI-compatible transport, and is stripped by :func:`anthropic_messages` for the SDK transport.
PROVIDER_REASONING_KEY = "provider_reasoning"


@dataclass
class LoopResult:
    text: str
    messages: list[dict[str, Any]]
    usage: Usage
    turns: int
    #: ``None`` when the model finished on its own, else :data:`STOP_MAX_TURNS` /
    #: :data:`STOP_NO_PROGRESS` / :data:`STOP_MAX_TOKENS`. Callers that report to the user must say
    #: so — the work done up to that point is real and already persisted, it is just not complete.
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
        max_truncated = self._config_int("llm_max_truncated_turns")
        if max_turns is None:
            max_turns = self._config_int("anthropic_max_tool_turns")
        channel = channel or str(project_id)

        convo = [dict(m) for m in messages]
        total = Usage()
        last_text = ""
        repeated: tuple[str, str] | None = None
        repeats = 0
        truncations = 0

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

            # A turn with no tool call is the model FINISHING only if the model chose to stop
            # (phase-64). Two things look identical to the old check and are not: a turn the
            # provider cut off at the output cap — a reasoning model that thought past
            # ``max_tokens`` produces exactly "no text, no tool call, finish_reason=length" — and an
            # empty response. Reading either as "done" is how a build phase that wrote nothing
            # came to be recorded complete. Nudge and continue, bounded; then stop honestly.
            if not turn.tool_uses and (turn.truncated or not turn.text.strip()):
                truncations += 1
                if truncations > max_truncated:
                    logger.warning(
                        "tool loop stopped: %d turn(s) produced nothing usable (%s)",
                        truncations,
                        turn.stop_reason,
                    )
                    return LoopResult(
                        text=last_text,
                        messages=convo,
                        usage=total,
                        turns=turn_index + 1,
                        stopped=STOP_MAX_TOKENS,
                    )
                if turn.text.strip():
                    # Partial prose is real model output; keep it so the model can continue it.
                    convo.append(_assistant_message(turn))
                    last_text = turn.text
                _append_user_text(convo, NUDGE_TRUNCATED)
                continue

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
        thinking: list[str] = []  # coalesced: a think arrives as thousands of tiny deltas
        thinking_len = 0

        async def flush_thinking() -> None:
            nonlocal thinking, thinking_len
            if thinking:
                await self._emit(channel, EventType.agent_reasoning, {"text": "".join(thinking)})
                thinking, thinking_len = [], 0

        await self._emit(channel, EventType.agent_stream_start, {"model": request.model})
        try:
            async for event in transport.stream(request):
                if isinstance(event, TextDelta):
                    if event.text:
                        await flush_thinking()
                        await self._emit(channel, EventType.agent_token, {"text": event.text})
                elif isinstance(event, ReasoningDelta):
                    # Its own event, never `agent.token`: reasoning is not the answer and must
                    # not be rendered as one — but it must be *visible*, or a long think reads as
                    # a hang (phase-64). Batched, so an 8k-token think does not evict the whole
                    # realtime ring one character at a time.
                    if event.text:
                        thinking.append(event.text)
                        thinking_len += len(event.text)
                        if thinking_len >= _REASONING_EMIT_CHARS:
                            await flush_thinking()
                else:
                    turn = event
            await flush_thinking()
        finally:
            await self._emit(
                channel,
                EventType.agent_stream_end,
                {
                    "model": request.model,
                    "completed": turn is not None,
                    "stop_reason": turn.stop_reason if turn is not None else None,
                    "reasoning_tokens": turn.usage.reasoning_tokens if turn is not None else 0,
                },
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
    """Rebuild the assistant turn as Anthropic content blocks for the next request.

    The provider's reasoning payload (phase-64) rides along under :data:`PROVIDER_REASONING_KEY`,
    beside ``content`` — never inside it, so the block vocabulary stays Anthropic-native.
    """
    content: list[dict[str, Any]] = []
    if turn.text:
        content.append({"type": "text", "text": turn.text})
    for call in turn.tool_uses:
        content.append({"type": "tool_use", "id": call.id, "name": call.name, "input": call.input})
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if turn.reasoning:
        message[PROVIDER_REASONING_KEY] = turn.reasoning
    return message


def _append_user_text(convo: list[dict[str, Any]], text: str) -> None:
    """Append ``text`` as a user turn, merging into a trailing user turn if there is one.

    Both APIs tolerate consecutive same-role turns, but a transcript that alternates cleanly is
    what every provider documents, so the merge keeps the corrective turn (phase-64) unremarkable.
    """
    block = {"type": "text", "text": text}
    if convo and convo[-1].get("role") == "user":
        last = convo[-1]
        content = last.get("content")
        if isinstance(content, list):
            last["content"] = [*content, block]
        else:
            last["content"] = [{"type": "text", "text": str(content or "")}, block]
        return
    convo.append({"role": "user", "content": [block]})


def anthropic_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The transcript as the Anthropic API accepts it: private loop-side keys stripped.

    Pure, so the SDK transport (which is never exercised in tests) delegates the one thing that
    could break its request shape to a function that is.
    """
    return [{k: v for k, v in m.items() if k != PROVIDER_REASONING_KEY} for m in messages]


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
            "messages": anthropic_messages(request.messages),
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


#: The reasoning fields an OpenAI-compatible endpoint may stream, and which are echoed back
#: verbatim on the next turn (phase-64). ``reasoning_details`` is OpenRouter's structured form;
#: ``reasoning_content`` is Z.ai / DeepSeek / vLLM's; ``reasoning`` is OpenRouter's plain text.
_REASONING_ECHO_KEYS: tuple[str, ...] = ("reasoning_details", "reasoning_content")
_REASONING_TEXT_KEYS: tuple[str, ...] = ("reasoning", "reasoning_content")


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
            # Echo the provider's reasoning back — and ONLY what this endpoint itself sent
            # (phase-64). OpenRouter / Z.ai / DeepSeek preserve chain-of-thought across tool calls
            # this way; an endpoint that never produced the field never receives it, so every
            # other provider's request stays byte-identical.
            reasoning = message.get(PROVIDER_REASONING_KEY)
            if isinstance(reasoning, dict):
                for key in _REASONING_ECHO_KEYS:
                    if reasoning.get(key):
                        assistant[key] = reasoning[key]
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


def _tool_uses_from_frags(frags: list[tuple[str, str, str]], *, truncated: bool) -> list[ToolUse]:
    """Tool calls from ``(id, name, raw_args)`` fragments.

    On a turn the provider cut off at the output cap (phase-64) a half-built argument JSON is the
    *expected* shape, not a provider fault: the call is dropped (logged) and the loop's
    truncation handling takes over. On any other stop reason malformed arguments still raise.
    """
    out: list[ToolUse] = []
    for index, (call_id, name, raw) in enumerate(frags):
        try:
            args = _parse_tool_args(raw)
        except ProviderError:
            if not truncated:
                raise
            logger.warning("dropping tool call %r cut off at the output cap", name)
            continue
        out.append(ToolUse(id=call_id or f"call_{index}", name=name, input=args))
    return out


class _ReasoningCollector:
    """Reassemble a turn's streamed reasoning into the payload the provider expects back."""

    def __init__(self) -> None:
        self._text: dict[str, list[str]] = {}
        self._details: dict[int, dict[str, Any]] = {}

    def take(self, delta: Any) -> str:
        """Fold one delta in; return the text chunk (if any) to stream to the client."""
        chunk = ""
        for key in _REASONING_TEXT_KEYS:
            piece = getattr(delta, key, None)
            if isinstance(piece, str) and piece:
                self._text.setdefault(key, []).append(piece)
                chunk += piece
        details = getattr(delta, "reasoning_details", None)
        for position, item in enumerate(details or []):
            data = _as_dict(item)
            if data is None:
                continue
            index = int(data.get("index", position) or 0)
            slot = self._details.get(index)
            if slot is None:
                self._details[index] = dict(data)
                continue
            # Fragments of one detail: string fields concatenate, everything else is from the first.
            for k, v in data.items():
                if isinstance(v, str) and isinstance(slot.get(k), str) and k not in ("type", "id"):
                    slot[k] += v
                elif k not in slot:
                    slot[k] = v
        return chunk

    def payload(self) -> dict[str, Any] | None:
        out: dict[str, Any] = {}
        for key, parts in self._text.items():
            out[key] = "".join(parts)
        if self._details:
            out["reasoning_details"] = [self._details[i] for i in sorted(self._details)]
        return out or None


def _as_dict(item: Any) -> dict[str, Any] | None:
    if isinstance(item, dict):
        return item
    dump = getattr(item, "model_dump", None)
    if callable(dump):
        try:
            data = dump()
        except Exception:  # pragma: no cover - defensive against exotic SDK objects
            return None
        return data if isinstance(data, dict) else None
    return None


def _usage_from(reported: Any) -> Usage:
    details = getattr(reported, "completion_tokens_details", None)
    reasoning = int(getattr(details, "reasoning_tokens", 0) or 0) if details is not None else 0
    return Usage(
        int(getattr(reported, "prompt_tokens", 0) or 0),
        int(getattr(reported, "completion_tokens", 0) or 0),
        reasoning,
    )


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
    reasoning = _ReasoningCollector()

    async for chunk in chunks:
        reported = getattr(chunk, "usage", None)
        if reported is not None:
            usage = _usage_from(reported)
        choices = getattr(chunk, "choices", None) or []
        if not choices:
            continue
        choice = choices[0]

        delta = getattr(choice, "delta", None)
        if delta is not None:
            thought = reasoning.take(delta)
            if thought:
                yield ReasoningDelta(thought)
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

    tool_uses = _tool_uses_from_frags(
        [(frags[i]["id"], frags[i]["name"], frags[i]["args"]) for i in sorted(frags)],
        truncated=stop_reason == "max_tokens",
    )
    yield TurnComplete(
        text="".join(text_parts),
        tool_uses=tool_uses,
        usage=usage,
        stop_reason=stop_reason,
        reasoning=reasoning.payload(),
    )


def _from_completion(response: Any) -> Iterator[StreamEvent]:
    """Fold a **non-streamed** Chat Completion into the same events the streamed path yields.

    The `no_stream` repair (phase-57) exists because some models/organizations refuse
    ``stream: true``. The loop, the agents and cost accounting must not be able to tell the two
    paths apart, so this emits one :class:`TextDelta` with the whole message followed by the same
    terminal :class:`TurnComplete` — sharing :func:`_parse_tool_args` and :data:`_STOP_REASONS` with
    :func:`_accumulate` so the two cannot drift. Only *live* token streaming is lost.
    """
    reported = getattr(response, "usage", None)
    usage = _usage_from(reported) if reported is not None else Usage()
    choices = getattr(response, "choices", None) or []
    if not choices:
        yield TurnComplete(usage=usage)
        return

    choice = choices[0]
    message = getattr(choice, "message", None)
    reasoning = _ReasoningCollector()
    thought = reasoning.take(message) if message is not None else ""
    if thought:
        yield ReasoningDelta(thought)
    text = str(getattr(message, "content", None) or "")
    if text:
        yield TextDelta(text)

    finish = getattr(choice, "finish_reason", None)
    stop_reason = _STOP_REASONS.get(finish, finish) if finish else "end_turn"
    tool_uses = _tool_uses_from_frags(
        [
            (
                getattr(call, "id", None) or "",
                getattr(getattr(call, "function", None), "name", "") or "",
                getattr(getattr(call, "function", None), "arguments", "") or "",
            )
            for call in getattr(message, "tool_calls", None) or []
        ],
        truncated=stop_reason == "max_tokens",
    )
    yield TurnComplete(
        text=text,
        tool_uses=tool_uses,
        usage=usage,
        stop_reason=stop_reason,
        reasoning=reasoning.payload(),
    )


#: Substrings marking a **mid-stream** provider failure as transient. An OpenAI-compatible server
#: (NVIDIA NIM, vLLM, Together, …) may accept a request (HTTP 200), open the SSE stream, and only
#: then report that it is out of capacity — e.g. "ResourceExhausted: Worker local total request
#: limit reached (16/16)". The SDK surfaces that as a bare ``APIError`` carrying **no status code**
#: (the response really was a 200), so its text is the only signal available to classify it.
#:
#: Timeouts belong here for the same reason, and are their own failure mode: a *broker* endpoint
#: (OpenRouter) fronts several upstreams per model id, and once it has opened the stream it can no
#: longer fail over to another one — ``allow_fallbacks`` only covers a failure at dispatch. So an
#: upstream that stalls arrives as "Upstream error from <provider>: Inference request timed out",
#: inside a 200. Unmatched, that read as a *rejection* and was raised on the first try; matched, it
#: costs a retry, and the retry re-enters the broker's routing — very often a different upstream.
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
    "timed out",
    "timeout",
    "deadline exceeded",
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


#: Request repairs learned this process, shared across the per-turn transport instances and seeded
#: from (and persisted back to) the ``llm_param_quirks`` setting.
_PARAM_MEMO = ParamMemo()


def _provider_message(exc: BaseException, *, produced: bool, repair: Repair | None = None) -> str:
    """A user-facing explanation for a failed provider call (rendered by the API as a 502)."""
    text = str(exc).strip() or exc.__class__.__name__
    if repair is not None:
        # The parameter was identified but repairing it did not help (or the repair budget ran
        # out). Naming it is the difference between an actionable message and the opaque one that
        # made the original report hard to diagnose.
        return (
            f"The model provider rejected the request over the {repair.param!r} parameter, and "
            f"adapting it did not help ({text}). Point MODEL_CODEGEN / MODEL_ROUTING at a "
            "different model, or clear the LLM_PARAM_QUIRKS setting if a stale learned repair is "
            "suspected."
        )
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


#: Host suffix identifying OpenRouter. Matched against the *host* of the configured base URL, not
#: anywhere in the string: a proxy whose path or query happens to mention OpenRouter is not
#: OpenRouter, and sending it a field it does not know is exactly the breakage this guards against.
_OPENROUTER_HOST_SUFFIX = "openrouter.ai"


def is_openrouter(base_url: str) -> bool:
    """Whether ``base_url`` (the configured ``OPENAI_BASE_URL``) points at OpenRouter.

    Pure and total: anything unparseable is simply *not* OpenRouter, so a malformed setting can
    only cost the routing preference, never a call.
    """
    text = (base_url or "").strip()
    if not text or text == "default":  # `run()`'s placeholder for "SDK default endpoint"
        return False
    # A base URL configured without a scheme ("openrouter.ai/api/v1") still has a host.
    candidate = text if "//" in text else f"//{text}"
    try:
        host = (urlsplit(candidate).hostname or "").lower()
    except ValueError:
        return False
    return host == _OPENROUTER_HOST_SUFFIX or host.endswith(f".{_OPENROUTER_HOST_SUFFIX}")


def build_openai_params(
    request: MessageRequest, *, repairs: Iterable[Repair] = (), endpoint: str = ""
) -> dict[str, Any]:
    """Translate the loop's Anthropic-shaped request into Chat Completions params.

    Pure, so the wire format is testable without a network or an SDK. ``repairs`` are the
    compatibility adaptations for this (endpoint, model) — seeded from the known-families table and
    learned from the provider's own errors (phase-57). They are applied last, so the canonical body
    is always built the same way and every provider-specific difference lives in one place.

    ``endpoint`` is the configured base URL. It is used for one thing: OpenRouter accepts a
    ``provider`` routing preference, and we ask it to sort candidate upstreams by price, cheapest
    first (cost discipline, golden rule 7). Every other endpoint gets a body byte-identical to the
    one it got before this existed — the field is added only when the *host* is OpenRouter's.
    """
    params: dict[str, Any] = {
        "model": request.model,
        "max_tokens": request.max_tokens,
        "messages": _to_openai_messages(request.system, request.messages),
        "stream": True,
        # Asks for the token-usage trailer so cost accounting has real numbers. Endpoints that
        # reject unknown fields (Mistral, Azure serverless) answer 422 and it is dropped as a
        # `drop` repair like any other — usage then reads zero there, which beats every call
        # failing outright.
        "stream_options": {"include_usage": True},
    }
    if request.tools:
        params["tools"] = _to_openai_tools(request.tools)
    reasoning = reasoning_params(
        str(get_config().get("llm_reasoning_effort")),
        int(get_config().get("llm_reasoning_max_tokens") or 0),
    )
    if reasoning:
        # A hint to the provider about how much to think (phase-64) — OpenRouter's unified
        # `reasoning` object, which it translates for each upstream (GLM's `thinking`, Anthropic's
        # budget, OpenAI's `reasoning_effort`). Only ever sent when an operator configured it, so
        # the default body is byte-identical to before; an endpoint that rejects it answers 400
        # naming `reasoning`, and the phase-57 ladder drops it and retries.
        extra_body = dict(params.get("extra_body") or {})
        extra_body["reasoning"] = reasoning
        params["extra_body"] = extra_body
    if is_openrouter(endpoint):
        # OpenRouter fronts several upstreams per model id and takes a routing preference in a
        # top-level `provider` object; `sort: "price"` means "prioritise lowest price" — cheapest
        # first (https://openrouter.ai/docs/features/provider-routing). `allow_fallbacks` stays at
        # its default `true`, so a cheap upstream being down still falls through to the next rather
        # than failing the call, and OpenRouter already restricts tool-bearing requests to upstreams
        # that support tool use — which the codegen loop depends on.
        #
        # It travels in `extra_body`, not as a top-level kwarg: the OpenAI SDK's `create()` has a
        # closed signature (no **kwargs), so `provider=...` would raise TypeError before any
        # request; `extra_body` is its documented escape hatch and is merged into the JSON body, so
        # the bytes on the wire are exactly OpenRouter's documented shape.
        extra_body = dict(params.get("extra_body") or {})
        extra_body["provider"] = {"sort": "price"}
        params["extra_body"] = extra_body
    return apply_repairs(params, repairs)


#: Accepted values of ``LLM_REASONING_EFFORT`` (blank = provider default).
REASONING_EFFORTS: tuple[str, ...] = ("", "none", "low", "medium", "high")


def reasoning_params(effort: str, max_tokens: int) -> dict[str, Any]:
    """The ``reasoning`` object for the configured effort/budget, or ``{}`` when unset.

    Pure. ``none`` disables thinking on models that allow it; an effort level and a token budget
    may be combined; anything unrecognised is ignored (a typo must cost the hint, never the call).
    """
    out: dict[str, Any] = {}
    level = (effort or "").strip().lower()
    if level == "none":
        out["enabled"] = False
    elif level in ("low", "medium", "high"):
        out["effort"] = level
    if max_tokens > 0:
        out["max_tokens"] = max_tokens
    return out


class OpenAITransport:
    """Transport over any OpenAI-compatible Chat Completions endpoint (phase-53).

    A pure adapter: it translates the loop's Anthropic-shaped request into Chat Completions params,
    streams, and folds the response back into the loop's :class:`StreamEvent`s. Like
    :class:`SdkTransport`, the real network path is lazy-imported and not exercised by tests; the
    translation, stream-reassembly and retry-classification helpers it delegates to are.
    """

    def __init__(self, memo: ParamMemo | None = None) -> None:
        # The memo is process-wide so a repair learned on one turn is applied on the next: the
        # transport itself is resolved per turn (so an admin can switch providers without a
        # restart), which would otherwise re-learn the same quirk on every call.
        self._memo = memo if memo is not None else _PARAM_MEMO
        self._memo.merge(parse_quirks(str(get_config().get("llm_param_quirks"))))

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

        async def create(params: dict[str, Any]) -> Any:
            return await client.chat.completions.create(**params)

        async for event in self.run(
            request, create, endpoint=base_url or "default", error_type=openai.OpenAIError
        ):
            yield event

    async def run(
        self,
        request: MessageRequest,
        create: Callable[[dict[str, Any]], Awaitable[Any]],
        *,
        endpoint: str,
        error_type: type[BaseException],
    ) -> AsyncIterator[StreamEvent]:
        """The retry + repair loop, with the network injected so it is testable without an SDK.

        Two independent bounded ladders, deliberately counted separately:

        - **repairs** (phase-57) fix a request the provider *described* as wrong — a 400 naming a
          parameter. They must not consume a transient-failure retry, because they are not failures
          in the same sense: the endpoint is healthy and told us exactly what to change.
        - **retries** handle a provider that is unhealthy, with backoff.
        """
        config = get_config()
        model = request.model
        # Seed rules spare a wasted first call on known families; learned repairs are authoritative
        # over them, because they came from this endpoint's own answer.
        repairs = _merge_repairs(seed_repairs(model), self._memo.get(endpoint, model))
        params = build_openai_params(request, repairs=repairs, endpoint=endpoint)

        learning = bool(config.get("openai_param_learning"))
        repair_cap = int(config.get("openai_max_param_repairs"))
        repairs_used = 0
        applied = {r.signature for r in repairs}

        # The retry spans **opening and consuming** the stream, not just opening it: a compatible
        # server can answer 200 and only then fail inside the SSE body, which is precisely the case
        # a create-only retry would miss.
        attempts = int(config.get("openai_max_retries"))
        attempt = 0
        while True:
            produced = False
            try:
                raw = await create(params)
                if params.get("stream", True):
                    async for event in _accumulate(raw):
                        produced = True
                        yield event
                else:
                    for event in _from_completion(raw):
                        produced = True
                        yield event
                return
            except error_type as exc:
                # A request the provider *described* as wrong is one we can repair. Once tokens have
                # been emitted the turn cannot be restarted — replaying it would duplicate streamed
                # output in the UI — so only a pre-output failure is ever repaired or retried.
                repair = None if produced else diagnose_param_error(exc, params)
                if repair is not None and repairs_used < repair_cap:
                    if repair.signature in applied:
                        # The same fix twice means it did not work; looping would be unbounded in
                        # everything but name. Stop and report.
                        raise ProviderError(
                            _provider_message(exc, produced=produced, repair=repair)
                        ) from exc
                    applied.add(repair.signature)
                    repairs_used += 1
                    params = apply_repairs(params, [repair])
                    logger.info(
                        "%s/%s rejected %r (%s); retrying with the %s repair applied",
                        endpoint,
                        model,
                        repair.param,
                        repair.reason[:200],
                        str(repair.adaptation),
                    )
                    if learning and self._memo.learn(endpoint, model, repair):
                        await persist_quirks(self._memo)
                    continue  # deliberately does NOT consume a transient retry
                if produced or attempt >= attempts or not _is_transient_openai_error(exc):
                    raise ProviderError(
                        _provider_message(exc, produced=produced, repair=repair)
                    ) from exc
                delay = _backoff_delay(attempt)
                attempt += 1
                # A silent retry chain looks identical to a hung request from the outside; say so.
                logger.warning(
                    "model provider unavailable, retrying in %.1fs (attempt %d/%d): %s",
                    delay,
                    attempt,
                    attempts,
                    exc,
                )
                await asyncio.sleep(delay)


def _merge_repairs(seed: list[Repair], learned: list[Repair]) -> list[Repair]:
    """Seed rules plus learned ones, with **learned winning** on the same (adaptation, param).

    A seed rule is a guess about a model family; a learned repair is this endpoint's own answer
    about this model. When they disagree, the answer wins.
    """
    merged = {r.signature: r for r in seed}
    merged.update({r.signature: r for r in learned})
    return list(merged.values())


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
