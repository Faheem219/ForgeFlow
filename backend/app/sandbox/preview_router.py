"""Live preview routes (phase-15). Ownership-checked via ProjectService."""

from __future__ import annotations

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends

from app.auth.deps import get_current_user_id
from app.db.models import Project
from app.projects.service import ProjectService, parse_object_id
from app.sandbox.preview import get_preview_service
from app.sandbox.schemas import PreviewInfo

router = APIRouter(prefix="/projects/{project_id}/preview", tags=["preview"])


async def _owned(project_id: str, user_id: PydanticObjectId) -> Project:
    return await ProjectService().get_owned(parse_object_id(project_id), user_id)


@router.get("/status", response_model=PreviewInfo)
async def preview_status(
    project_id: str, user_id: PydanticObjectId = Depends(get_current_user_id)
) -> PreviewInfo:
    project = await _owned(project_id, user_id)
    return await get_preview_service().status(project)


@router.post("/start", response_model=PreviewInfo)
async def preview_start(
    project_id: str, user_id: PydanticObjectId = Depends(get_current_user_id)
) -> PreviewInfo:
    project = await _owned(project_id, user_id)
    return await get_preview_service().start(project)


@router.post("/restart", response_model=PreviewInfo)
async def preview_restart(
    project_id: str, user_id: PydanticObjectId = Depends(get_current_user_id)
) -> PreviewInfo:
    project = await _owned(project_id, user_id)
    return await get_preview_service().restart(project)


@router.post("/stop", response_model=PreviewInfo)
async def preview_stop(
    project_id: str, user_id: PydanticObjectId = Depends(get_current_user_id)
) -> PreviewInfo:
    project = await _owned(project_id, user_id)
    return await get_preview_service().stop(project)
