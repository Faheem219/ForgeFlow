"""Stitch generation-quota tracking (phase-17).

Stitch's free tier is ~350 generations/month against the **platform** account, so we persist a small
per-window counter in ``forgeflow_meta`` (survives restarts; visible to phase-46 cost surfaces),
pre-check before spending a generation, and fail soft at the cap with a classified ``ProviderError``
+ a ``design.quota`` realtime event. ``fetch_code``/``refine`` metadata reads are free; only actual
generations count.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from pymongo import ReturnDocument

from app.core.config import get_config
from app.db.models import DesignQuota
from app.db.models.common import utcnow
from app.design.stitch_errors import PROVIDER_KEY, StitchErrorKind, stitch_error
from app.realtime.hub import emit
from app.realtime.schemas import EventType

# Reserved realtime channel for platform-wide (non-project) design signals. Phase-19 additionally
# surfaces quota on the active project's channel; here it stays project-independent by design —
# quota is global to the platform account, and DesignProvider methods carry no project context.
PLATFORM_CHANNEL = "platform"


class QuotaStatus(StrEnum):
    ok = "ok"
    degraded = "degraded"  # approaching the cap (used/limit >= soft ratio)
    exhausted = "exhausted"


@dataclass(frozen=True)
class QuotaSnapshot:
    provider: str
    window: str
    used: int
    limit: int
    status: QuotaStatus

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    def as_event(self) -> dict[str, object]:
        # Safe, token-free payload for the design.quota event.
        return {
            "provider": self.provider,
            "window": self.window,
            "used": self.used,
            "limit": self.limit,
            "remaining": self.remaining,
            "status": str(self.status),
        }


def current_window() -> str:
    """Monthly window key, e.g. ``2026-07`` (UTC)."""
    return datetime.now(UTC).strftime("%Y-%m")


class StitchQuota:
    def __init__(self, provider: str = PROVIDER_KEY) -> None:
        self._provider = provider

    def _limit(self) -> int:
        return int(get_config().get("stitch_quota_monthly"))

    def _classify(self, used: int, limit: int) -> QuotaStatus:
        if limit <= 0 or used >= limit:
            return QuotaStatus.exhausted
        if used / limit >= float(get_config().get("stitch_quota_soft_ratio")):
            return QuotaStatus.degraded
        return QuotaStatus.ok

    async def _count(self, window: str) -> int:
        doc = await DesignQuota.find_one({"provider": self._provider, "window": window})
        return doc.used if doc is not None else 0

    async def snapshot(self) -> QuotaSnapshot:
        window = current_window()
        used = await self._count(window)
        limit = self._limit()
        return QuotaSnapshot(self._provider, window, used, limit, self._classify(used, limit))

    async def check_available(self) -> QuotaSnapshot:
        """Pre-flight before spending a generation. Fail soft (+ event) when exhausted."""
        snap = await self.snapshot()
        if snap.status is QuotaStatus.exhausted:
            await self._emit(snap)
            raise stitch_error(
                StitchErrorKind.quota,
                f"Stitch quota exhausted ({snap.used}/{snap.limit} for {snap.window})",
            )
        return snap

    async def record_usage(self) -> QuotaSnapshot:
        """Atomically count one generation and emit the updated snapshot."""
        window = current_window()
        updated = await DesignQuota.get_motor_collection().find_one_and_update(
            {"provider": self._provider, "window": window},
            {"$inc": {"used": 1}, "$set": {"updated_at": utcnow()}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        used = int(updated["used"])
        limit = self._limit()
        snap = QuotaSnapshot(self._provider, window, used, limit, self._classify(used, limit))
        await self._emit(snap)
        return snap

    async def _emit(self, snap: QuotaSnapshot) -> None:
        await emit(PLATFORM_CHANNEL, EventType.design_quota, snap.as_event())
