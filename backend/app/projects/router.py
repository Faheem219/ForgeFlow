"""Project + stage-state routes (phase-06). All routes are ownership-checked via the service."""

from __future__ import annotations

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, Query

from app.auth.deps import get_current_user_id
from app.db.models.enums import ArtifactType, Stage
from app.orchestrator.artifacts import ArtifactService
from app.orchestrator.messages import MessageService
from app.orchestrator.schemas import ArtifactPublic, MessagePublic
from app.projects.schemas import (
    ProjectCreateRequest,
    ProjectDeleteResponse,
    ProjectPublic,
    ProjectRenameRequest,
    StageStatePublic,
    StageTransitionRequest,
    StageTransitionResponse,
)
from app.projects.service import ProjectService, parse_object_id

router = APIRouter(prefix="/projects", tags=["projects"])


@router.post("", status_code=201, response_model=ProjectPublic)
async def create_project(
    body: ProjectCreateRequest, user_id: PydanticObjectId = Depends(get_current_user_id)
) -> ProjectPublic:
    project = await ProjectService().create_project(user_id, body.name)
    return ProjectPublic.from_project(project)


@router.get("", response_model=list[ProjectPublic])
async def list_projects(
    user_id: PydanticObjectId = Depends(get_current_user_id),
) -> list[ProjectPublic]:
    projects = await ProjectService().list_for_user(user_id)
    return [ProjectPublic.from_project(p) for p in projects]


@router.get("/{project_id}", response_model=ProjectPublic)
async def get_project(
    project_id: str, user_id: PydanticObjectId = Depends(get_current_user_id)
) -> ProjectPublic:
    project = await ProjectService().get_owned(parse_object_id(project_id), user_id)
    return ProjectPublic.from_project(project)


@router.patch("/{project_id}", response_model=ProjectPublic)
async def rename_project(
    project_id: str,
    body: ProjectRenameRequest,
    user_id: PydanticObjectId = Depends(get_current_user_id),
) -> ProjectPublic:
    project = await ProjectService().rename(parse_object_id(project_id), user_id, body.name)
    return ProjectPublic.from_project(project)


@router.delete("/{project_id}", response_model=ProjectDeleteResponse)
async def delete_project(
    project_id: str, user_id: PydanticObjectId = Depends(get_current_user_id)
) -> ProjectDeleteResponse:
    """Delete a project and everything it owns: sandbox, app database, and all metadata.

    Returns a report rather than a bare ``204`` — external teardown fails soft, and a caller that
    cannot see *what* was left behind has no way to tell the user.
    """
    pid = parse_object_id(project_id)
    report = await ProjectService().delete(pid, user_id)
    return ProjectDeleteResponse.of(str(pid), report)


@router.get("/{project_id}/stages", response_model=list[StageStatePublic])
async def list_stages(
    project_id: str, user_id: PydanticObjectId = Depends(get_current_user_id)
) -> list[StageStatePublic]:
    states = await ProjectService().list_stages(parse_object_id(project_id), user_id)
    return [StageStatePublic.from_state(s) for s in states]


@router.post("/{project_id}/stages/{stage}/transition", response_model=StageTransitionResponse)
async def transition_stage(
    project_id: str,
    stage: Stage,
    body: StageTransitionRequest,
    user_id: PydanticObjectId = Depends(get_current_user_id),
) -> StageTransitionResponse:
    outcome = await ProjectService().transition_stage(
        parse_object_id(project_id), user_id, stage, body.action
    )
    return StageTransitionResponse.from_outcome(outcome)


@router.get("/{project_id}/messages", response_model=list[MessagePublic])
async def list_messages(
    project_id: str,
    stage: Stage | None = Query(default=None),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    user_id: PydanticObjectId = Depends(get_current_user_id),
) -> list[MessagePublic]:
    pid = parse_object_id(project_id)
    await ProjectService().get_owned(pid, user_id)
    messages = await MessageService().list_for_project(pid, stage=stage, skip=skip, limit=limit)
    return [MessagePublic.from_message(m) for m in messages]


@router.get("/{project_id}/artifacts", response_model=list[ArtifactPublic])
async def list_artifacts(
    project_id: str,
    stage: Stage | None = Query(default=None),
    artifact_type: ArtifactType | None = Query(default=None, alias="type"),
    user_id: PydanticObjectId = Depends(get_current_user_id),
) -> list[ArtifactPublic]:
    pid = parse_object_id(project_id)
    await ProjectService().get_owned(pid, user_id)
    artifacts = await ArtifactService().list_for_project(
        pid, stage=stage, artifact_type=artifact_type
    )
    return [ArtifactPublic.from_artifact(a) for a in artifacts]
