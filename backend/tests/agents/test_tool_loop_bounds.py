"""Hitting the tool loop's bound is an *unfinished* build, not a lost one.

The loop used to raise `UserError: Agent tool loop exceeded N turns without finishing`, which threw
away a build report for work that was already written and committed — the stage just failed and the
user had no record of what got built. The bound stays (D7: never an unbounded loop); what changes is
that the loop reports the stop and the caller presents it honestly.

A no-progress stop is the same idea applied earlier: a model repeating one identical call will keep
doing it until the bound, spending real tokens per turn.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
from beanie import PydanticObjectId

from app.agents.anthropic_client import (
    REPEAT_LIMIT,
    STOP_MAX_TURNS,
    STOP_NO_PROGRESS,
    AnthropicClient,
    LoopResult,
    MessageRequest,
    StreamEvent,
    ToolUse,
    TurnComplete,
)
from app.agents.cost import Usage
from app.agents.models import TaskKind
from app.core.config import reset_config
from app.db.models import Run

pytestmark = pytest.mark.usefixtures("mongo_db")


@dataclass
class _Call:
    name: str
    args: dict[str, object]


class LoopingTransport:
    """Answers every turn with a tool call, so the loop can only end at a bound."""

    def __init__(self, calls: list[_Call] | None = None) -> None:
        self.turns = 0
        self._calls = calls

    async def stream(self, request: MessageRequest) -> AsyncIterator[StreamEvent]:
        index = self.turns
        self.turns += 1
        call = (
            self._calls[min(index, len(self._calls) - 1)]
            if self._calls
            else _Call("write_file", {"path": f"src/f{index}.ts", "content": "x"})
        )
        yield TurnComplete(
            text=f"working ({index})",
            tool_uses=[ToolUse(id=f"t{index}", name=call.name, input=call.args)],
            usage=Usage(input_tokens=10, output_tokens=5),
            stop_reason="tool_use",
        )


async def _run() -> Run:
    return await Run(project_id=PydanticObjectId(), kind="test").insert()


async def _dispatch(name: str, tool_input: dict[str, object]) -> str:
    return "ok"


async def _loop(transport: LoopingTransport, run: Run) -> LoopResult:
    assert run.project_id is not None
    return await AnthropicClient(transport=transport).run_tool_loop(
        task_kind=TaskKind.codegen,
        project_id=run.project_id,
        run=run,
        messages=[{"role": "user", "content": "build it"}],
        tools=[{"name": "write_file"}],
        tool_dispatch=_dispatch,
    )


async def test_the_turn_bound_returns_instead_of_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_MAX_TOOL_TURNS", "4")
    reset_config()
    transport = LoopingTransport()

    result = await _loop(transport, await _run())

    assert result.stopped == STOP_MAX_TURNS
    assert result.finished is False
    assert result.turns == 4
    assert transport.turns == 4  # the bound is still a bound
    # The transcript and the last thing the model said survive, so a report can be built from them.
    assert "working" in result.text
    assert result.messages


async def test_the_loop_stops_when_the_model_repeats_itself(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_MAX_TOOL_TURNS", "50")
    reset_config()
    # The same call forever — a model going nowhere expensively.
    transport = LoopingTransport([_Call("read_file", {"path": "src/app.ts"})])

    result = await _loop(transport, await _run())

    assert result.stopped == STOP_NO_PROGRESS
    assert transport.turns == REPEAT_LIMIT  # stopped early, not at turn 50
    assert result.usage.total_tokens > 0


async def test_varied_work_is_never_mistaken_for_being_stuck(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Writing a different file each turn is exactly what a real build looks like."""
    monkeypatch.setenv("ANTHROPIC_MAX_TOOL_TURNS", "6")
    reset_config()
    transport = LoopingTransport()  # a distinct path per turn

    result = await _loop(transport, await _run())

    assert result.stopped == STOP_MAX_TURNS  # ran the full bound
    assert transport.turns == 6


async def test_the_same_tool_with_different_arguments_keeps_going(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_MAX_TOOL_TURNS", "5")
    reset_config()
    transport = LoopingTransport(
        [
            _Call("run_command", {"cmd": ["pnpm", "install"]}),
            _Call("run_command", {"cmd": ["pnpm", "build"]}),
            _Call("run_command", {"cmd": ["pnpm", "test"]}),
            _Call("run_command", {"cmd": ["git", "status"]}),
            _Call("run_command", {"cmd": ["pnpm", "dev"]}),
        ]
    )

    result = await _loop(transport, await _run())

    assert result.stopped == STOP_MAX_TURNS
    assert transport.turns == 5


async def test_the_default_bound_is_high_enough_for_a_real_build() -> None:
    """One turn per file write, so a full-stack app needs a bound in the hundreds, not tens."""
    reset_config()
    from app.core.config import get_config

    assert int(get_config().get("anthropic_max_tool_turns")) >= 100
