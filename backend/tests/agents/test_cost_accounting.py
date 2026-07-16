"""Token/₹ accounting: per-call recording, pricing, and per-project/global rollups (phase-20)."""

from __future__ import annotations

import pytest
from beanie import PydanticObjectId

from app.agents.cost import Usage, global_spend_inr, project_spend_inr, record_cost
from app.agents.pricing import price_inr
from app.core.config import reset_config
from app.db.models import Run

pytestmark = pytest.mark.usefixtures("mongo_db")


def test_price_uses_default_rates() -> None:
    # sonnet defaults: ₹250/Mtok in, ₹1250/Mtok out.
    assert price_inr("claude-sonnet-5", 1_000_000, 0) == pytest.approx(250.0)
    assert price_inr("claude-sonnet-5", 0, 1_000_000) == pytest.approx(1250.0)


def test_unpriced_model_costs_zero() -> None:
    assert price_inr("mystery-model", 1_000_000, 1_000_000) == 0.0


def test_pricing_override_from_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODEL_PRICING_JSON", '{"m1": {"input": 1000, "output": 2000}}')
    reset_config()
    assert price_inr("m1", 1_000_000, 0) == pytest.approx(1000.0)
    assert price_inr("m1", 0, 500_000) == pytest.approx(1000.0)


async def test_record_cost_accrues_onto_the_run() -> None:
    run = await Run(project_id=PydanticObjectId(), kind="agent").insert()

    inr1 = await record_cost(run, "claude-sonnet-5", Usage(1_000_000, 0))
    inr2 = await record_cost(run, "claude-sonnet-5", Usage(0, 1_000_000))

    assert inr1 == pytest.approx(250.0)
    assert inr2 == pytest.approx(1250.0)

    reloaded = await Run.get(run.id)
    assert reloaded is not None
    assert reloaded.cost.tokens == 2_000_000
    assert reloaded.cost.inr == pytest.approx(1500.0)


async def test_project_and_global_rollups() -> None:
    p1, p2 = PydanticObjectId(), PydanticObjectId()
    r1 = await Run(project_id=p1, kind="a").insert()
    r2 = await Run(project_id=p1, kind="b").insert()
    r3 = await Run(project_id=p2, kind="c").insert()
    await record_cost(r1, "claude-sonnet-5", Usage(1_000_000, 0))  # ₹250
    await record_cost(r2, "claude-sonnet-5", Usage(1_000_000, 0))  # ₹250
    await record_cost(r3, "claude-sonnet-5", Usage(0, 1_000_000))  # ₹1250

    assert await project_spend_inr(p1) == pytest.approx(500.0)
    assert await project_spend_inr(p2) == pytest.approx(1250.0)
    assert await global_spend_inr() == pytest.approx(1750.0)
