"""Cost accounting (phase-20): accrue token/₹ spend onto the active :class:`Run` and roll it up per
project / globally (the numbers the eval harness and cost dashboard read, phase-44/46).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from beanie import PydanticObjectId

from app.agents.pricing import price_inr
from app.db.models import Run


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def plus(self, other: Usage) -> Usage:
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
        )


async def record_cost(run: Run, model: str, usage: Usage) -> float:
    """Add one call's tokens + ₹ to ``run`` (persisted). Returns the ₹ charged."""
    inr = price_inr(model, usage.input_tokens, usage.output_tokens)
    run.cost.tokens += usage.total_tokens
    run.cost.inr += inr
    await run.save()
    return inr


async def _sum_inr(match: dict[str, Any]) -> float:
    pipeline = [{"$match": match}, {"$group": {"_id": None, "total": {"$sum": "$cost.inr"}}}]
    docs = await Run.get_motor_collection().aggregate(pipeline).to_list(length=1)
    return float(docs[0]["total"]) if docs else 0.0


async def project_spend_inr(project_id: PydanticObjectId) -> float:
    return await _sum_inr({"project_id": project_id})


async def global_spend_inr() -> float:
    return await _sum_inr({})
