"""Project + stage-state routes (phase-06). All routes are ownership-checked via the service."""

from __future__ import annotations

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends

from app.auth.deps import get_current_user_id
from app.db.models.enums import Stage
from app.projects.schemas import (
    ProjectCreateRequest,
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


@router.delete("/{project_id}", status_code=204)
async def delete_project(
    project_id: str, user_id: PydanticObjectId = Depends(get_current_user_id)
) -> None:
    await ProjectService().delete(parse_object_id(project_id), user_id)


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
