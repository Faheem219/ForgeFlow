"""Infra API (phase-33) — classify a project's deployment shape and read the plan back.

- ``POST /projects/{id}/infra/analyze`` — inspect the workspace and persist a versioned
  ``infra_plan`` artifact.
- ``GET  /projects/{id}/infra/plan`` — the latest plan, or ``null`` before the first analysis.

Planning only: nothing here deploys anything (that is phase-35/37).
"""

from __future__ import annotations

import json
from typing import Any

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.auth.deps import get_current_user_id
from app.core.limits import expensive_rate_limit
from app.db.models.enums import ArtifactType, Stage
from app.deploy.analyzer import INFRA_PLAN_KIND, InfraAnalyzer
from app.orchestrator.artifacts import ArtifactService
from app.projects.service import ProjectService, parse_object_id

infra_router = APIRouter(prefix="/projects", tags=["infra"])


class InfraPlanPublic(BaseModel):
    fe: dict[str, Any] | None = None
    be: dict[str, Any] | None = None
    db: dict[str, Any] | None = None
    required_secrets: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    confidence: str = "high"
    notes: list[str] = Field(default_factory=list)
    needs_confirmation: bool = False


@infra_router.post(
    "/{project_id}/infra/analyze",
    response_model=InfraPlanPublic,
    dependencies=[Depends(expensive_rate_limit)],
)
async def analyze_infra(
    project_id: str, user_id: PydanticObjectId = Depends(get_current_user_id)
) -> InfraPlanPublic:
    """Classify the workspace and persist the resulting plan."""
    project = await ProjectService().get_owned(parse_object_id(project_id), user_id)
    plan = await InfraAnalyzer().analyze(project)
    return InfraPlanPublic.model_validate(plan.to_dict())


@infra_router.get("/{project_id}/infra/plan", response_model=InfraPlanPublic | None)
async def get_infra_plan(
    project_id: str, user_id: PydanticObjectId = Depends(get_current_user_id)
) -> InfraPlanPublic | None:
    """The most recent plan, or ``null`` when the project has never been analyzed."""
    pid = parse_object_id(project_id)
    await ProjectService().get_owned(pid, user_id)

    artifacts = ArtifactService()
    latest = await artifacts.get_latest(pid, Stage.deploy, ArtifactType.infra_plan)
    if latest is None or latest.meta.get("kind") != INFRA_PLAN_KIND:
        return None
    content = await artifacts.get_content(latest)
    if not content:
        return None
    try:
        return InfraPlanPublic.model_validate(json.loads(content))
    except (ValueError, TypeError):  # a corrupt plan must not 500 the deploy stage
        return None
