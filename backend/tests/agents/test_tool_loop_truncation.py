"""A turn the provider cut off is not the model finishing (phase-64).

The loop used to return ``finished`` on *any* turn without a tool call. A reasoning model that
thinks past ``max_tokens`` produces exactly that — no text, no tool call, ``finish_reason=length``
— and so does an empty response. Both were read as "done", which is how a build phase that wrote
nothing came to be recorded complete. Now the loop nudges and continues, bounded, and only then
stops — honestly, as unfinished.
"""

from __future__ import annotations

import copy
from collections.abc import AsyncIterator

import pytest
from beanie import PydanticObjectId

from app.agents.anthropic_client import (
    NUDGE_TRUNCATED,
    STOP_MAX_TOKENS,
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


class ScriptedTransport:
    """Replays the given turns in order and records every request it was handed."""

    def __init__(self, turns: list[TurnComplete]) -> None:
        self._turns = list(turns)
        self.requests: list[MessageRequest] = []

    async def stream(self, request: MessageRequest) -> AsyncIterator[StreamEvent]:
        # The loop hands the SAME transcript list to every request; snapshot it, or every
        # recorded request would show the final transcript.
        self.requests.append(copy.deepcopy(request))
        yield self._turns.pop(0)


def _truncated(text: str = "") -> TurnComplete:
    """What a reasoning model that thought past the cap looks like on the wire."""
    return TurnComplete(
        text=text, usage=Usage(100, 8192, reasoning_tokens=8190), stop_reason="max_tokens"
    )


def _write(index: int = 0) -> TurnComplete:
    return TurnComplete(
        text="",
        tool_uses=[ToolUse(id=f"t{index}", name="write_file", input={"path": f"f{index}.ts"})],
        usage=Usage(100, 300),
        stop_reason="tool_use",
    )


def _done() -> TurnComplete:
    return TurnComplete(text="Wrote the file.", usage=Usage(50, 10), stop_reason="end_turn")


async def _run() -> Run:
    return await Run(project_id=PydanticObjectId(), kind="test").insert()


async def _loop(transport: ScriptedTransport, run: Run) -> LoopResult:
    dispatched: list[str] = []

    async def dispatch(name: str, tool_input: dict[str, object]) -> str:
        dispatched.append(name)
        return '{"ok": true}'

    assert run.project_id is not None
    result = await AnthropicClient(transport=transport).run_tool_loop(
        task_kind=TaskKind.codegen,
        project_id=run.project_id,
        run=run,
        messages=[{"role": "user", "content": "build it"}],
        tools=[{"name": "write_file"}],
        tool_dispatch=dispatch,
    )
    result.dispatched = dispatched  # type: ignore[attr-defined]
    return result


@pytest.fixture(autouse=True)
def _two_nudges(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_MAX_TRUNCATED_TURNS", "2")
    reset_config()


async def test_a_truncated_turn_is_nudged_and_the_loop_continues() -> None:
    transport = ScriptedTransport([_truncated(), _write(), _done()])

    result = await _loop(transport, await _run())

    assert result.finished is True
    assert result.text == "Wrote the file."
    assert result.dispatched == ["write_file"]  # type: ignore[attr-defined]
    assert transport.requests[0].messages == [{"role": "user", "content": "build it"}]
    # The second request carries the corrective turn — and no fabricated assistant turn for the
    # empty one (there was nothing to append).
    second = transport.requests[1].messages
    assert [m["role"] for m in second] == ["user"]
    assert NUDGE_TRUNCATED in str(second[-1]["content"])


async def test_an_empty_end_turn_is_treated_the_same_way() -> None:
    empty = TurnComplete(text="   ", usage=Usage(10, 0), stop_reason="end_turn")
    transport = ScriptedTransport([empty, _write(), _done()])

    result = await _loop(transport, await _run())

    assert result.finished is True
    assert result.dispatched == ["write_file"]  # type: ignore[attr-defined]
    assert NUDGE_TRUNCATED in str(transport.requests[1].messages[-1]["content"])


async def test_partial_prose_survives_and_is_followed_by_the_nudge() -> None:
    """A model cut off mid-sentence said something real; keep it so it can continue."""
    transport = ScriptedTransport([_truncated("Here is the plan: first"), _write(), _done()])

    await _loop(transport, await _run())

    second = transport.requests[1].messages
    assert [m["role"] for m in second] == ["user", "assistant", "user"]
    assert second[1]["content"] == [{"type": "text", "text": "Here is the plan: first"}]
    assert NUDGE_TRUNCATED in str(second[2]["content"])


async def test_the_nudge_is_bounded_and_the_loop_then_stops_unfinished() -> None:
    transport = ScriptedTransport([_truncated(), _truncated(), _truncated(), _write(), _done()])

    result = await _loop(transport, await _run())

    assert result.stopped == STOP_MAX_TOKENS
    assert result.finished is False
    assert len(transport.requests) == 3  # two nudges, then the third truncation ends it
    assert result.dispatched == []  # type: ignore[attr-defined]


async def test_a_truncated_turn_that_still_carries_a_whole_tool_call_dispatches_it() -> None:
    """Only the trailing text was cut; the call is complete and must run."""
    whole = TurnComplete(
        text="",
        tool_uses=[ToolUse(id="t", name="write_file", input={"path": "a.ts"})],
        usage=Usage(100, 8192),
        stop_reason="max_tokens",
    )
    transport = ScriptedTransport([whole, _done()])

    result = await _loop(transport, await _run())

    assert result.finished is True
    assert result.dispatched == ["write_file"]  # type: ignore[attr-defined]
    assert len(transport.requests) == 2  # no nudge was needed


async def test_every_nudged_turn_is_still_paid_for() -> None:
    """Cost discipline: a truncated turn burned real tokens and the Run must say so."""
    run = await _run()
    transport = ScriptedTransport([_truncated(), _write(), _done()])

    result = await _loop(transport, run)

    assert result.usage.output_tokens == 8192 + 300 + 10
    assert result.usage.reasoning_tokens == 8190
    reloaded = await Run.get(run.id)
    assert reloaded is not None
    assert reloaded.cost.tokens == result.usage.total_tokens
    assert reloaded.cost.reasoning_tokens == 8190


async def test_zero_nudges_means_the_first_truncation_stops_the_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_MAX_TRUNCATED_TURNS", "0")
    reset_config()
    transport = ScriptedTransport([_truncated(), _write(), _done()])

    result = await _loop(transport, await _run())

    assert result.stopped == STOP_MAX_TOKENS
    assert len(transport.requests) == 1
