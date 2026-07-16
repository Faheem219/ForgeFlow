"""Claude client (phase-20): async Messages, streaming, and a bounded tool-use loop, with per-call
cost accounting and budget guardrails baked in.

The transport (the thing that actually talks to Anthropic) sits behind a :class:`Transport` Protocol
so the whole loop is testable against a scripted fake — the concrete SDK transport is lazy-imported
(the ``anthropic`` package is only needed for real calls) and never touched by tests.

Flow per turn: enforce budget → stream the turn (text deltas → ``agent.token`` events) → record cost
on the ``Run`` → enforce budget again → if the model called tools, dispatch them and feed results
back, else return. The loop is bounded by ``anthropic_max_tool_turns``.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from beanie import PydanticObjectId

from app.agents.budget import enforce_budget
from app.agents.cost import Usage, record_cost
from app.agents.models import TaskKind, route
from app.core.config import get_config
from app.core.errors import ProviderError, UserError
from app.db.models import Run
from app.realtime.hub import emit
from app.realtime.schemas import EventType

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


@dataclass
class LoopResult:
    text: str
    messages: list[dict[str, Any]]
    usage: Usage
    turns: int


# --------------------------------------------------------------------- client


class AnthropicClient:
    def __init__(
        self,
        transport: Transport | None = None,
        *,
        emitter: Emitter = emit,
    ) -> None:
        self._transport = transport if transport is not None else SdkTransport()
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
    ) -> LoopResult:
        """Drive a bounded tool-use loop and return the final assistant text + full transcript."""
        model = route(task_kind)
        max_tokens = self._config_int("anthropic_max_output_tokens")
        max_turns = self._config_int("anthropic_max_tool_turns")
        channel = channel or str(project_id)

        convo = [dict(m) for m in messages]
        total = Usage()

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

            if not turn.tool_uses:
                return LoopResult(text=turn.text, messages=convo, usage=total, turns=turn_index + 1)

            if tool_dispatch is None:
                raise UserError("The model requested a tool but no tool dispatcher was provided")

            results: list[dict[str, Any]] = []
            for call in turn.tool_uses:
                output = await tool_dispatch(call.name, call.input)
                results.append({"type": "tool_result", "tool_use_id": call.id, "content": output})
            convo.append({"role": "user", "content": results})

        raise UserError(f"Agent tool loop exceeded {max_turns} turns without finishing")

    async def _stream_turn(self, request: MessageRequest, channel: str) -> TurnComplete:
        turn: TurnComplete | None = None
        async for event in self._transport.stream(request):
            if isinstance(event, TextDelta):
                if event.text:
                    await self._emit(channel, EventType.agent_token, {"text": event.text})
            else:
                turn = event
        if turn is None:
            raise ProviderError("Anthropic stream ended without a completion")
        return turn


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
        client = anthropic.AsyncAnthropic(
            api_key=str(config.get("anthropic_api_key")), base_url=base_url
        )

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
                await asyncio.sleep(min(20.0, 0.5 * 2**attempt) + random.uniform(0, 0.25))
        raise ProviderError("Anthropic request failed")  # unreachable
