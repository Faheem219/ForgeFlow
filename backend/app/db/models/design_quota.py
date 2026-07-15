from __future__ import annotations

from datetime import datetime
from typing import ClassVar

import pymongo
from beanie import Document
from pydantic import Field
from pymongo import IndexModel

from app.db.models.common import utcnow


class DesignQuota(Document):
    """A per-provider, per-window generation counter for the platform-owned design account.

    Persisted in ``forgeflow_meta`` so quota survives restarts and is visible to the cost /
    observability surfaces (phase-46). One doc per ``(provider, window)`` (e.g. ``stitch``/
    ``2026-07``); incremented atomically on each successful generation (phase-17).
    """

    provider: str
    window: str  # window key, e.g. "2026-07" (monthly)
    used: int = 0  # generations spent in this window (named 'used', not 'count', to not shadow
    # Beanie's Document.count() classmethod)
    updated_at: datetime = Field(default_factory=utcnow)

    class Settings:
        name = "design_quotas"
        indexes: ClassVar[list[IndexModel]] = [
            IndexModel(
                [("provider", pymongo.ASCENDING), ("window", pymongo.ASCENDING)],
                unique=True,
                name="uniq_provider_window",
            ),
        ]
