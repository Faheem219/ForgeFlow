from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar

import pymongo
from beanie import Document, PydanticObjectId
from pydantic import Field
from pymongo import IndexModel

from app.db.models.common import utcnow
from app.db.models.enums import MessageRole, Stage


class Message(Document):
    project_id: PydanticObjectId
    stage: Stage | None = None
    role: MessageRole
    content: str
    artifacts: list[PydanticObjectId] = Field(default_factory=list)
    token_usage: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)

    class Settings:
        name = "messages"
        indexes: ClassVar[list[IndexModel]] = [
            IndexModel(
                [("project_id", pymongo.ASCENDING), ("created_at", pymongo.ASCENDING)],
                name="msg_project_created",
            ),
        ]
