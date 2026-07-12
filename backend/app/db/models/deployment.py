from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar

import pymongo
from beanie import Document, PydanticObjectId
from pydantic import Field
from pymongo import IndexModel

from app.db.models.common import utcnow
from app.db.models.enums import DeployMode


class Deployment(Document):
    project_id: PydanticObjectId
    mode: DeployMode
    fe_target: str | None = None
    be_target: str | None = None
    db_target: str | None = None
    urls: dict[str, str] = Field(default_factory=dict)
    status: str = "pending"
    topology_snapshot: dict[str, Any] = Field(default_factory=dict)
    logs_ref: str | None = None
    created_at: datetime = Field(default_factory=utcnow)

    class Settings:
        name = "deployments"
        indexes: ClassVar[list[IndexModel]] = [
            IndexModel(
                [("project_id", pymongo.ASCENDING), ("created_at", pymongo.DESCENDING)],
                name="deploy_project_created",
            ),
        ]
