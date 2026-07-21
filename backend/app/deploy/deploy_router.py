"""Deploy read API (phase-38) — what the topology view hydrates from.

The graph follows live ``deploy.status`` events while a deploy runs, but a reload has no events to
replay, so it hydrates from the last persisted ``Deployment`` instead — which is why these are
read-only:

- ``GET /projects/{id}/deploy/latest`` — the most recent deployment (topology, URLs, status), or
  ``null`` before the first deploy.
- ``GET /projects/{id}/deploy/latest/logs`` — that deployment's step log, for the node drawer.

Deploying is *not* here: it runs through the conductor (``POST /projects/{id}/intent`` with
``stage=deploy``), so stage legality — the deploy⇐build hard prereq (§8) — stays enforced in exactly
one place. Nothing in these payloads is sensitive: a ``Deployment`` records URLs and topology, never
env values (§7).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.auth.deps import get_current_user_id
from app.db.blobs import get_blob_store
from app.db.models import Deployment
from app.db.models.enums import DeployMode
from app.projects.service import ProjectService, parse_object_id

deploy_router = APIRouter(prefix="/projects", tags=["deploy"])


class DeploymentPublic(BaseModel):
    """A deployment as the topology UI sees it — URLs and shape, no env values."""

    id: str
    mode: DeployMode
    status: str
    fe_target: str | None = None
    be_target: str | None = None
    db_target: str | None = None
    urls: dict[str, str] = Field(default_factory=dict)
    topology_snapshot: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class DeployLogsPublic(BaseModel):
    """``log`` is empty when the deploy stored none (or the blob has since gone)."""

    deployment_id: str
    log: str


async def _latest(project_id: str, user_id: PydanticObjectId) -> Deployment | None:
    pid = parse_object_id(project_id)
    await ProjectService().get_owned(pid, user_id)
    # Mongo stores datetimes at millisecond precision, so two deploys can tie on ``created_at``;
    # ``_id`` is monotonic and breaks the tie in real insertion order.
    return await Deployment.find({"project_id": pid}).sort("-created_at", "-_id").first_or_none()


def _to_public(deployment: Deployment) -> DeploymentPublic:
    return DeploymentPublic(
        id=str(deployment.id),
        mode=deployment.mode,
        status=deployment.status,
        fe_target=deployment.fe_target,
        be_target=deployment.be_target,
        db_target=deployment.db_target,
        urls=deployment.urls,
        topology_snapshot=deployment.topology_snapshot,
        created_at=deployment.created_at,
    )


@deploy_router.get("/{project_id}/deploy/latest", response_model=DeploymentPublic | None)
async def latest_deployment(
    project_id: str, user_id: PydanticObjectId = Depends(get_current_user_id)
) -> DeploymentPublic | None:
    """The most recent deployment, or ``null`` when the project has never been deployed."""
    deployment = await _latest(project_id, user_id)
    return None if deployment is None else _to_public(deployment)


@deploy_router.get("/{project_id}/deploy/latest/logs", response_model=DeployLogsPublic | None)
async def latest_deployment_logs(
    project_id: str, user_id: PydanticObjectId = Depends(get_current_user_id)
) -> DeployLogsPublic | None:
    """The step log the last deploy recorded (db/be/fe lines), for the per-node log view."""
    deployment = await _latest(project_id, user_id)
    if deployment is None:
        return None

    log = ""
    if deployment.logs_ref:
        try:
            log = (await get_blob_store().get(deployment.logs_ref)).decode("utf-8")
        except Exception:  # a missing blob is an empty log, not a broken panel
            log = ""
    return DeployLogsPublic(deployment_id=str(deployment.id), log=log)
