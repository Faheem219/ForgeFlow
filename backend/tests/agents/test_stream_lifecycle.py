"""Stream lifecycle events bracket every model turn.

``agent.token`` on its own is not a protocol: a client receiving deltas cannot tell "the model
paused" from "the turn is over", so any UI state keyed to token arrival never clears. These tests
pin the bracket — including on the failure paths, which is where a missing terminal event does the
real damage (a spinner that runs forever *after* the work visibly finished).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from beanie import PydanticObjectId

from app.agents.anthropic_client import (
    AnthropicClient,
    MessageRequest,
    StreamEvent,
    TextDelta,
    TurnComplete,
)
from app.agents.cost import Usage
from app.agents.models import TaskKind
from app.core.errors import ProviderError
from app.db.models import Run
from app.realtime.hub import get_hub
from app.realtime.schemas import EventType
from tests.agents.test_client_tool_loop import FakeTransport, ScriptedTurn

pytestmark = pytest.mark.usefixtures("mongo_db")


class ExplodingTransport:
    """Dies mid-stream, after emitting some text — the ugly real-world case."""

    async def stream(self, request: MessageRequest) -> AsyncIterator[StreamEvent]:
        yield TextDelta("partial output")
        raise ProviderError("upstream connection reset")


class TruncatedTransport:
    """Ends cleanly but never yields a TurnComplete."""

    async def stream(self, request: MessageRequest) -> AsyncIterator[StreamEvent]:
        yield TextDelta("half a thought")


async def _run() -> Run:
    return await Run(project_id=PydanticObjectId(), kind="test").insert()


async def _drain(queue: object) -> list[tuple[str, dict[str, object]]]:
    events: list[tuple[str, dict[str, object]]] = []
    while not queue.empty():  # type: ignore[attr-defined]
        event = queue.get_nowait()  # type: ignore[attr-defined]
        events.append((event.event, event.payload))
    return events


async def test_stream_is_bracketed_by_start_and_end() -> None:
    run = await _run()
    project_id = run.project_id
    assert project_id is not None

    transport = FakeTransport(
        [
            ScriptedTurn(
                deltas=["Hello ", "world"],
                turn=TurnComplete(text="Hello world", usage=Usage(10, 5)),
            )
        ]
    )

    hub = get_hub()
    async with hub.subscription(str(project_id)) as queue:
        await AnthropicClient(transport).run_tool_loop(
            task_kind=TaskKind.codegen,
            project_id=project_id,
            run=run,
            messages=[{"role": "user", "content": "hi"}],
        )
        events = await _drain(queue)

    names = [name for name, _ in events]
    assert names.index(str(EventType.agent_stream_start)) < names.index(str(EventType.agent_token))
    assert names.index(str(EventType.agent_token)) < names.index(str(EventType.agent_stream_end))

    end_payload = next(p for name, p in events if name == str(EventType.agent_stream_end))
    assert end_payload["completed"] is True


async def test_each_turn_gets_its_own_bracket() -> None:
    """Two turns must produce two brackets — otherwise a client cannot reset per-turn text."""
    run = await _run()
    project_id = run.project_id
    assert project_id is not None

    from app.agents.anthropic_client import ToolUse

    transport = FakeTransport(
        [
            ScriptedTurn(
                deltas=["thinking"],
                turn=TurnComplete(
                    text="thinking",
                    tool_uses=[ToolUse(id="t1", name="read_file", input={"path": "a.ts"})],
                    usage=Usage(10, 5),
                    stop_reason="tool_use",
                ),
            ),
            ScriptedTurn(deltas=["done"], turn=TurnComplete(text="done", usage=Usage(5, 2))),
        ]
    )

    async def dispatch(name: str, tool_input: dict[str, object]) -> str:
        return "contents"

    hub = get_hub()
    async with hub.subscription(str(project_id)) as queue:
        await AnthropicClient(transport).run_tool_loop(
            task_kind=TaskKind.codegen,
            project_id=project_id,
            run=run,
            messages=[{"role": "user", "content": "read a.ts"}],
            tools=[{"name": "read_file", "input_schema": {}}],
            tool_dispatch=dispatch,
        )
        events = await _drain(queue)

    names = [name for name, _ in events]
    assert names.count(str(EventType.agent_stream_start)) == 2
    assert names.count(str(EventType.agent_stream_end)) == 2


async def test_stream_end_is_emitted_when_the_transport_raises() -> None:
    """The failure path is the one that matters: no end event ⇒ the client streams forever."""
    run = await _run()
    project_id = run.project_id
    assert project_id is not None

    hub = get_hub()
    async with hub.subscription(str(project_id)) as queue:
        with pytest.raises(ProviderError):
            await AnthropicClient(ExplodingTransport()).run_tool_loop(
                task_kind=TaskKind.codegen,
                project_id=project_id,
                run=run,
                messages=[{"role": "user", "content": "hi"}],
            )
        events = await _drain(queue)

    names = [name for name, _ in events]
    assert str(EventType.agent_stream_end) in names
    end_payload = next(p for name, p in events if name == str(EventType.agent_stream_end))
    assert end_payload["completed"] is False


async def test_stream_end_is_emitted_when_the_turn_never_completes() -> None:
    run = await _run()
    project_id = run.project_id
    assert project_id is not None

    hub = get_hub()
    async with hub.subscription(str(project_id)) as queue:
        with pytest.raises(ProviderError):
            await AnthropicClient(TruncatedTransport()).run_tool_loop(
                task_kind=TaskKind.codegen,
                project_id=project_id,
                run=run,
                messages=[{"role": "user", "content": "hi"}],
            )
        events = await _drain(queue)

    end_payload = next(p for name, p in events if name == str(EventType.agent_stream_end))
    assert end_payload["completed"] is False


# --------------------------------------------------------------------- reasoning (phase-64)


class ThinkingTransport:
    """A reasoning model: a long think in tiny deltas, then a short answer."""

    async def stream(self, request: MessageRequest) -> AsyncIterator[StreamEvent]:
        from app.agents.anthropic_client import ReasoningDelta

        for piece in ["Let ", "me ", "think ", "about ", "this."]:
            yield ReasoningDelta(piece)
        yield TextDelta("Done.")
        yield TurnComplete(
            text="Done.", usage=Usage(10, 50, reasoning_tokens=40), stop_reason="end_turn"
        )


async def test_reasoning_is_emitted_as_its_own_batched_event_and_never_as_tokens() -> None:
    """A two-minute think with no event at all reads as a hang; a think rendered as the answer is
    wrong. It gets its own event, coalesced (a real think is thousands of one-word deltas)."""
    run = await _run()
    project_id = run.project_id
    assert project_id is not None

    hub = get_hub()
    async with hub.subscription(str(project_id)) as queue:
        await AnthropicClient(ThinkingTransport()).run_tool_loop(
            task_kind=TaskKind.codegen,
            project_id=project_id,
            run=run,
            messages=[{"role": "user", "content": "go"}],
        )
        events = await _drain(queue)

    kinds = [kind for kind, _ in events]
    assert kinds == [
        str(EventType.agent_stream_start),
        str(EventType.agent_reasoning),  # one event for the whole (short) think
        str(EventType.agent_token),
        str(EventType.agent_stream_end),
    ]
    reasoning = next(payload for kind, payload in events if kind == str(EventType.agent_reasoning))
    assert reasoning["text"] == "Let me think about this."
    end = events[-1][1]
    assert end["completed"] is True
    assert end["stop_reason"] == "end_turn"
    assert end["reasoning_tokens"] == 40
