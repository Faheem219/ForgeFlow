"""Sandbox lifecycle routes (phase-11). Every route is ownership-checked via ProjectService."""

from __future__ import annotations

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends

from app.auth.deps import get_current_user_id
from app.core.limits import expensive_rate_limit
from app.db.models import Project
from app.projects.service import ProjectService, parse_object_id
from app.sandbox.manager import get_manager
from app.sandbox.schemas import SandboxDestroyRequest, SandboxInfo

router = APIRouter(prefix="/projects/{project_id}/sandbox", tags=["sandbox"])


async def _owned(project_id: str, user_id: PydanticObjectId) -> Project:
    return await ProjectService().get_owned(parse_object_id(project_id), user_id)


@router.get("/status", response_model=SandboxInfo)
async def sandbox_status(
    project_id: str, user_id: PydanticObjectId = Depends(get_current_user_id)
) -> SandboxInfo:
    project = await _owned(project_id, user_id)
    return await get_manager().status(project)


@router.post("/ensure", response_model=SandboxInfo, dependencies=[Depends(expensive_rate_limit)])
async def sandbox_ensure(
    project_id: str, user_id: PydanticObjectId = Depends(get_current_user_id)
) -> SandboxInfo:
    project = await _owned(project_id, user_id)
    return await get_manager().ensure(project)


@router.post("/stop", response_model=SandboxInfo)
async def sandbox_stop(
    project_id: str, user_id: PydanticObjectId = Depends(get_current_user_id)
) -> SandboxInfo:
    project = await _owned(project_id, user_id)
    return await get_manager().stop(project)


@router.post("/destroy", response_model=SandboxInfo)
async def sandbox_destroy(
    project_id: str,
    body: SandboxDestroyRequest | None = None,
    user_id: PydanticObjectId = Depends(get_current_user_id),
) -> SandboxInfo:
    project = await _owned(project_id, user_id)
    remove_volume = body.remove_volume if body is not None else False
    return await get_manager().destroy(project, remove_volume=remove_volume)
