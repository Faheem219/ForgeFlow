"""Per-model pricing (phase-20). Rates are **config**, not code, so they update without a deploy
and are admin-editable (phase-51/52). Rates are ₹ per 1,000,000 tokens, split input/output.

An unpriced model costs ₹0 (never a crash) — observability will show 0 spend, prompting the operator
to price it. Budgets can only be enforced for models that are priced.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from app.core.config import get_config
from app.core.errors import SystemError  # noqa: A004 - taxonomy name fixed by the plan

_MTOK = 1_000_000.0


@dataclass(frozen=True)
class ModelPrice:
    input_inr_per_mtok: float
    output_inr_per_mtok: float


# Placeholder ₹/Mtok defaults — override with MODEL_PRICING_JSON for real rates.
DEFAULT_PRICING: dict[str, ModelPrice] = {
    "claude-sonnet-5": ModelPrice(250.0, 1250.0),
    "claude-haiku-4-5-20251001": ModelPrice(70.0, 350.0),
    # OpenAI-compatible placeholders (phase-53). Countless compatible ids exist, so an unlisted
    # one prices at ₹0 until MODEL_PRICING_JSON supplies its rate (the operator-priced path above).
    "gpt-4o": ModelPrice(210.0, 840.0),
    "gpt-4o-mini": ModelPrice(12.0, 50.0),
}


def pricing_map() -> dict[str, ModelPrice]:
    raw = str(get_config().get("model_pricing_json")).strip()
    if not raw:
        return DEFAULT_PRICING
    try:
        data = json.loads(raw)
        return {
            model: ModelPrice(float(rates["input"]), float(rates["output"]))
            for model, rates in data.items()
        }
    except (ValueError, KeyError, TypeError) as exc:
        raise SystemError(f"Malformed MODEL_PRICING_JSON: {exc}") from exc


def price_inr(model: str, input_tokens: int, output_tokens: int) -> float:
    """₹ cost of a call. Unknown model → 0.0 (must be priced to count against a budget)."""
    price = pricing_map().get(model)
    if price is None:
        return 0.0
    return (
        input_tokens / _MTOK * price.input_inr_per_mtok
        + output_tokens / _MTOK * price.output_inr_per_mtok
    )
