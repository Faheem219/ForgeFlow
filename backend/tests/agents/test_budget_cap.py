"""Budget caps halt runs with a clear error + budget.halt event; nothing runs past the cap."""

from __future__ import annotations

import pytest
from beanie import PydanticObjectId

from app.agents.anthropic_client import AnthropicClient, TextDelta, TurnComplete
from app.agents.budget import enforce_budget
from app.agents.cost import Usage, record_cost
from app.agents.models import TaskKind
from app.core.config import reset_config
from app.core.errors import UserError
from app.db.models import Run
from app.realtime.hub import get_hub
from app.realtime.schemas import EventType
from tests.agents.test_client_tool_loop import FakeTransport, ScriptedTurn

pytestmark = pytest.mark.usefixtures("mongo_db")


def _cap(
    monkeypatch: pytest.MonkeyPatch, *, project: str | None = None, global_: str | None = None
) -> None:
    if project is not None:
        monkeypatch.setenv("BUDGET_CAP_INR_PER_PROJECT", project)
    if global_ is not None:
        monkeypatch.setenv("BUDGET_CAP_INR_GLOBAL", global_)
    reset_config()


async def _spend(project_id: PydanticObjectId, inr_tokens: int) -> None:
    run = await Run(project_id=project_id, kind="prior").insert()
    await record_cost(run, "claude-sonnet-5", Usage(0, inr_tokens))  # output-priced spend


async def test_under_cap_returns_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    _cap(monkeypatch, project="1000")
    pid = PydanticObjectId()
    snap = await enforce_budget(pid)
    assert snap.breached_scope() is None


async def test_project_cap_halts_with_error_and_event(monkeypatch: pytest.MonkeyPatch) -> None:
    _cap(monkeypatch, project="0.5")
    pid = PydanticObjectId()
    await _spend(pid, 1_000_000)  # ₹1250 output → over the ₹0.5 cap
    hub = get_hub()

    async with hub.subscription(str(pid)) as queue:
        with pytest.raises(UserError, match="project"):
            await enforce_budget(pid)
        event = await queue.get()

    assert event.event == str(EventType.budget_halt)
    assert event.payload["scope"] == "project"


async def test_global_cap_halts(monkeypatch: pytest.MonkeyPatch) -> None:
    _cap(monkeypatch, global_="0.5")
    pid = PydanticObjectId()
    await _spend(pid, 1_000_000)

    with pytest.raises(UserError, match="global"):
        await enforce_budget(pid)


async def test_loop_halts_before_calling_the_transport_when_already_over(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _cap(monkeypatch, project="0.5")
    pid = PydanticObjectId()
    await _spend(pid, 1_000_000)  # already over the cap
    run = await Run(project_id=pid, kind="agent").insert()

    transport = FakeTransport(
        [ScriptedTurn(deltas=["hi"], turn=TurnComplete(text="hi", usage=Usage(1, 1)))]
    )

    with pytest.raises(UserError):
        await AnthropicClient(transport).run_tool_loop(
            task_kind=TaskKind.codegen,
            project_id=pid,
            run=run,
            messages=[{"role": "user", "content": "go"}],
        )

    # The pre-call budget check fired first — the model was never contacted.
    assert transport.requests == []


async def test_loop_halts_after_a_call_that_crosses_the_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Cap ₹1.0; one turn spends ₹1250 (1M output tokens) → the AFTER check halts.
    _cap(monkeypatch, project="1.0")
    pid = PydanticObjectId()
    run = await Run(project_id=pid, kind="agent").insert()

    transport = FakeTransport(
        [
            ScriptedTurn(
                deltas=[TextDelta("x").text],
                turn=TurnComplete(text="x", usage=Usage(0, 1_000_000)),
            )
        ]
    )

    with pytest.raises(UserError):
        await AnthropicClient(transport).run_tool_loop(
            task_kind=TaskKind.codegen,
            project_id=pid,
            run=run,
            messages=[{"role": "user", "content": "go"}],
        )

    # The call happened (one request) and its cost was recorded before the halt.
    assert len(transport.requests) == 1
    reloaded = await Run.get(run.id)
    assert reloaded is not None and reloaded.cost.inr == pytest.approx(1250.0)
