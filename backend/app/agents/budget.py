"""Budget guardrails (phase-20, §7, D4, risk §12).

Before and after every model call, the client checks the per-project and global ₹ caps. On breach it
**halts** the run with a clear :class:`UserError` and emits a ``budget.halt`` event — nothing runs
past the cap. Caps are config-resolved (``BUDGET_CAP_INR_PER_PROJECT`` / ``BUDGET_CAP_INR_GLOBAL``),
so the admin dashboard can change them (phase-51/52).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from beanie import PydanticObjectId

from app.agents.cost import global_spend_inr, project_spend_inr
from app.core.config import get_config
from app.core.errors import UserError
from app.realtime.hub import emit
from app.realtime.schemas import EventType

# (channel, event_type, payload) -> awaitable
Emitter = Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class BudgetSnapshot:
    project_inr: float
    project_cap: float | None
    global_inr: float
    global_cap: float | None

    def breached_scope(self) -> str | None:
        if self.project_cap is not None and self.project_inr >= self.project_cap:
            return "project"
        if self.global_cap is not None and self.global_inr >= self.global_cap:
            return "global"
        return None

    def warning_scope(self, ratio: float) -> str | None:
        """The scope approaching its cap, if any (phase-46).

        Halting at the cap with no prior signal is a surprise; this is the signal. Checked only
        below the cap — once breached, ``breached_scope`` is the louder and more accurate answer.
        """
        if ratio <= 0 or self.breached_scope() is not None:
            return None
        if self.project_cap is not None and self.project_inr >= self.project_cap * ratio:
            return "project"
        if self.global_cap is not None and self.global_inr >= self.global_cap * ratio:
            return "global"
        return None

    def headroom(self) -> dict[str, float | None]:
        """What is left before each cap — ``None`` where no cap is configured."""
        return {
            "project": (
                None if self.project_cap is None else round(self.project_cap - self.project_inr, 6)
            ),
            "global": (
                None if self.global_cap is None else round(self.global_cap - self.global_inr, 6)
            ),
        }

    def as_event(self, scope: str) -> dict[str, Any]:
        return {
            "scope": scope,
            "project_inr": round(self.project_inr, 6),
            "project_cap": self.project_cap,
            "global_inr": round(self.global_inr, 6),
            "global_cap": self.global_cap,
        }


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


async def budget_snapshot(project_id: PydanticObjectId) -> BudgetSnapshot:
    config = get_config()
    return BudgetSnapshot(
        project_inr=await project_spend_inr(project_id),
        project_cap=_optional_float(config.get("budget_cap_inr_per_project")),
        global_inr=await global_spend_inr(),
        global_cap=_optional_float(config.get("budget_cap_inr_global")),
    )


async def enforce_budget(
    project_id: PydanticObjectId, *, emitter: Emitter = emit
) -> BudgetSnapshot:
    """Raise + emit ``budget.halt`` if a cap is reached; otherwise return the snapshot."""
    snapshot = await budget_snapshot(project_id)

    warn_scope = snapshot.warning_scope(float(get_config().get("budget_warn_ratio")))
    if warn_scope is not None:
        await emitter(
            str(project_id),
            EventType.budget_warning,
            {**snapshot.as_event(warn_scope), "headroom": snapshot.headroom()},
        )

    scope = snapshot.breached_scope()
    if scope is not None:
        await emitter(str(project_id), EventType.budget_halt, snapshot.as_event(scope))
        cap = snapshot.project_cap if scope == "project" else snapshot.global_cap
        spent = snapshot.project_inr if scope == "project" else snapshot.global_inr
        raise UserError(
            f"Budget cap reached ({scope}: ₹{spent:.2f} of ₹{cap:.2f}). "
            "The run was halted — raise the cap in settings to continue."
        )
    return snapshot
