from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar

import pymongo
from beanie import Document, PydanticObjectId
from pydantic import Field
from pymongo import IndexModel

from app.db.models.common import utcnow
from app.db.models.enums import SettingCategory


class PlatformSetting(Document):
    """Admin-editable platform config; the highest-priority layer of the resolver (phase-51)."""

    key: str
    value: Any = None  # JSON-serializable; Fernet ciphertext when `encrypted` is set
    category: SettingCategory
    #: Whether ``value`` is Fernet ciphertext (§7: a secret set from the panel is never stored, or
    #: read back, in plaintext). Decryption happens at read time in ``app.core.config_db``.
    encrypted: bool = False
    updated_by: PydanticObjectId | None = None
    updated_at: datetime = Field(default_factory=utcnow)

    class Settings:
        name = "platform_settings"
        indexes: ClassVar[list[IndexModel]] = [
            IndexModel([("key", pymongo.ASCENDING)], unique=True, name="uniq_setting_key"),
        ]
