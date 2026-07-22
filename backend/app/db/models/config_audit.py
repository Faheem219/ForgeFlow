from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar

import pymongo
from beanie import Document, PydanticObjectId
from pydantic import Field
from pymongo import IndexModel

from app.db.models.common import utcnow


class ConfigAudit(Document):
    """One admin config change, kept for accountability (phase-51).

    Records who changed which key, the value before and after, and when — so a platform setting that
    turns out to be wrong (a budget cap, a model id) can always be traced to a person and a moment.
    """

    key: str
    action: str  # "update" | "delete"
    before: Any = None  # JSON-serializable; the effective value before the change
    after: Any = None  # the value written (``None`` for a delete → revert to env/default)
    updated_by: PydanticObjectId | None = None
    created_at: datetime = Field(default_factory=utcnow)

    class Settings:
        name = "config_audit"
        indexes: ClassVar[list[IndexModel]] = [
            IndexModel(
                [("key", pymongo.ASCENDING), ("created_at", pymongo.DESCENDING)],
                name="config_audit_key_created",
            ),
        ]
