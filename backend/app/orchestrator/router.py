"""Top-level artifact routes (phase-07): fetch a single artifact + diff two artifacts.

Ownership is enforced via the artifact's owning project — an artifact the caller doesn't own is
reported as ``404`` (its existence is never leaked).
"""

from __future__ import annotations

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends

from app.auth.deps import get_current_user_id
from app.core.errors import NotFoundError
from app.db.models import Artifact
from app.orchestrator.artifacts import ArtifactService
from app.orchestrator.conductor import Conductor
from app.orchestrator.schemas import (
    ArtifactDetail,
    ArtifactDiffResponse,
    Intent,
    IntentRequest,
    IntentResponse,
)
from app.projects.service import ProjectService, parse_object_id

router = APIRouter(prefix="/artifacts", tags=["artifacts"])
intents_router = APIRouter(prefix="/projects", tags=["orchestrator"])


async def _owned_artifact(artifact_id: str, user_id: PydanticObjectId) -> Artifact:
    artifact = await ArtifactService().get_by_id(parse_object_id(artifact_id, "Artifact"))
    if artifact is None:
        raise NotFoundError("Artifact not found")
    try:
        await ProjectService().get_owned(artifact.project_id, user_id)
    except NotFoundError as exc:
        # Don't leak that the artifact exists under someone else's project.
        raise NotFoundError("Artifact not found") from exc
    return artifact


@router.get("/{artifact_id}", response_model=ArtifactDetail)
async def get_artifact(
    artifact_id: str, user_id: PydanticObjectId = Depends(get_current_user_id)
) -> ArtifactDetail:
    artifact = await _owned_artifact(artifact_id, user_id)
    content = await ArtifactService().get_content(artifact)
    return ArtifactDetail.from_artifact_with_content(artifact, content)


@router.get("/{a}/diff/{b}", response_model=ArtifactDiffResponse)
async def diff_artifacts(
    a: str, b: str, user_id: PydanticObjectId = Depends(get_current_user_id)
) -> ArtifactDiffResponse:
    artifact_a = await _owned_artifact(a, user_id)
    artifact_b = await _owned_artifact(b, user_id)
    diff = await ArtifactService().diff(artifact_a, artifact_b)
    return ArtifactDiffResponse(a=str(artifact_a.id), b=str(artifact_b.id), diff=diff)


@intents_router.post("/{project_id}/intent", response_model=IntentResponse)
async def submit_intent(
    project_id: str,
    body: IntentRequest,
    user_id: PydanticObjectId = Depends(get_current_user_id),
) -> IntentResponse:
    """Route a user intent through the conductor; detailed progress streams over the WS."""
    intent = Intent.from_request(parse_object_id(project_id), body)
    return await Conductor().handle_intent(user_id, intent)
