from __future__ import annotations

from datetime import datetime
from typing import ClassVar

import pymongo
from beanie import Document, PydanticObjectId
from pydantic import Field
from pymongo import IndexModel

from app.db.models.common import utcnow
from app.db.models.enums import CredentialKind, CredentialScope


class Credential(Document):
    """A provider token. Value is encrypted at rest (encryption lands in phase-34)."""

    user_id: PydanticObjectId
    kind: CredentialKind
    encrypted_secret: str
    scope: CredentialScope
    created_at: datetime = Field(default_factory=utcnow)

    class Settings:
        name = "credentials"
        indexes: ClassVar[list[IndexModel]] = [
            IndexModel([("user_id", pymongo.ASCENDING)], name="cred_user"),
        ]
