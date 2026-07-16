"""A streamed, multi-turn tool-use loop against a scripted fake transport (phase-20)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
from beanie import PydanticObjectId

from app.agents.anthropic_client import (
    AnthropicClient,
    MessageRequest,
    StreamEvent,
    TextDelta,
    ToolUse,
    TurnComplete,
)
from app.agents.cost import Usage
from app.agents.models import TaskKind
from app.db.models import Run
from app.realtime.hub import get_hub
from app.realtime.schemas import EventType

pytestmark = pytest.mark.usefixtures("mongo_db")


@dataclass
class ScriptedTurn:
    deltas: list[str]
    turn: TurnComplete


class FakeTransport:
    """Replays scripted turns and records the requests it was handed."""

    def __init__(self, turns: list[ScriptedTurn]) -> None:
        self._turns = list(turns)
        self.requests: list[MessageRequest] = []

    async def stream(self, request: MessageRequest) -> AsyncIterator[StreamEvent]:
        self.requests.append(request)
        script = self._turns.pop(0)
        for delta in script.deltas:
            yield TextDelta(delta)
        yield script.turn


async def _run() -> Run:
    return await Run(project_id=PydanticObjectId(), kind="test").insert()


async def test_streamed_tool_loop_completes() -> None:
    run = await _run()
    project_id = run.project_id
    assert project_id is not None

    transport = FakeTransport(
        [
            ScriptedTurn(
                deltas=["Let me ", "look."],
                turn=TurnComplete(
                    text="Let me look.",
                    tool_uses=[ToolUse(id="t1", name="read_file", input={"path": "a.ts"})],
                    usage=Usage(input_tokens=100, output_tokens=20),
                    stop_reason="tool_use",
                ),
            ),
            ScriptedTurn(
                deltas=["All done."],
                turn=TurnComplete(text="All done.", usage=Usage(input_tokens=50, output_tokens=10)),
            ),
        ]
    )

    dispatched: list[tuple[str, dict[str, object]]] = []

    async def dispatch(name: str, tool_input: dict[str, object]) -> str:
        dispatched.append((name, tool_input))
        return "export const x = 1;"

    tokens: list[str] = []
    hub = get_hub()
    async with hub.subscription(str(project_id)) as queue:
        result = await AnthropicClient(transport).run_tool_loop(
            task_kind=TaskKind.codegen,
            project_id=project_id,
            run=run,
            messages=[{"role": "user", "content": "read a.ts"}],
            tools=[{"name": "read_file", "input_schema": {}}],
            tool_dispatch=dispatch,
        )
        while not queue.empty():
            event = queue.get_nowait()
            if event.event == str(EventType.agent_token):
                tokens.append(str(event.payload["text"]))

    # Two turns; tool dispatched with the model's input; final text returned.
    assert result.turns == 2
    assert result.text == "All done."
    assert dispatched == [("read_file", {"path": "a.ts"})]

    # Every text delta was streamed as an agent.token event.
    assert tokens == ["Let me ", "look.", "All done."]

    # The transcript threads the tool call + result back to the model.
    tool_result_msgs = [
        m
        for m in result.messages
        if m["role"] == "user"
        and isinstance(m["content"], list)
        and m["content"]
        and m["content"][0].get("type") == "tool_result"
    ]
    assert (
        tool_result_msgs and tool_result_msgs[0]["content"][0]["content"] == "export const x = 1;"
    )

    # Cost accrued across both turns (150 in + 30 out).
    reloaded = await Run.get(run.id)
    assert reloaded is not None
    assert reloaded.cost.tokens == 180
    assert reloaded.cost.inr > 0


async def test_tool_request_without_a_dispatcher_is_a_user_error() -> None:
    run = await _run()
    assert run.project_id is not None
    transport = FakeTransport(
        [
            ScriptedTurn(
                deltas=[],
                turn=TurnComplete(
                    tool_uses=[ToolUse(id="t1", name="x", input={})],
                    usage=Usage(1, 1),
                    stop_reason="tool_use",
                ),
            )
        ]
    )
    from app.core.errors import UserError

    with pytest.raises(UserError):
        await AnthropicClient(transport).run_tool_loop(
            task_kind=TaskKind.codegen,
            project_id=run.project_id,
            run=run,
            messages=[{"role": "user", "content": "hi"}],
        )
