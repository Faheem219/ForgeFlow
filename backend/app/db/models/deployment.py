from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar

import pymongo
from beanie import Document, PydanticObjectId
from pydantic import BaseModel, Field
from pymongo import IndexModel

from app.db.models.common import utcnow
from app.db.models.enums import DeployMode

#: ``Deployment.status`` value for a deployment the user has taken down (phase-62). Distinct from
#: ``failed``: the hosting is gone deliberately, and the record is kept as the audit trail.
STATUS_DELETED = "deleted"


class DeploymentRef(BaseModel):
    """The provider-side handle for one deployed target (phase-62).

    Every post-deploy provider operation — build logs, teardown, a status re-check — keys off the
    deployment id, which lived only inside the orchestrator's call frame until now and was discarded
    when it returned. Secret-safe by construction: an opaque id and a project name, never a token.
    """

    provider: str
    #: Vercel: the deployment id. Render: the service id.
    id: str
    #: Provider-side project/service name — Vercel scopes env vars to the project, not a deployment.
    project: str | None = None


class Deployment(Document):
    project_id: PydanticObjectId
    mode: DeployMode
    fe_target: str | None = None
    be_target: str | None = None
    db_target: str | None = None
    urls: dict[str, str] = Field(default_factory=dict)
    #: Provider handles per target key (``"fe"`` / ``"be"``). Empty on every record written before
    #: phase-62, so consumers must degrade (no provider logs, no teardown) rather than fail.
    refs: dict[str, DeploymentRef] = Field(default_factory=dict)
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
