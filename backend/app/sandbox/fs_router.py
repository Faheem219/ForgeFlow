"""Workspace filesystem + git routes (phase-12). Ownership-checked via ProjectService.

Kept in its own router because these live under ``/projects/{id}/fs`` and
``/projects/{id}/sandbox/git`` rather than the sandbox-lifecycle prefix.
"""

from __future__ import annotations

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, Query

from app.auth.deps import get_current_user_id
from app.db.models import Project
from app.projects.service import ProjectService, parse_object_id
from app.sandbox.schemas import (
    FileContent,
    FileNode,
    GitCommitRequest,
    GitCommitResponse,
    GitInfo,
    MkdirRequest,
    MoveRequest,
    WriteFileRequest,
)
from app.sandbox.workspace import WorkspaceService

router = APIRouter(prefix="/projects/{project_id}", tags=["workspace"])


async def _owned(project_id: str, user_id: PydanticObjectId) -> Project:
    return await ProjectService().get_owned(parse_object_id(project_id), user_id)


@router.get("/fs/tree", response_model=list[FileNode])
async def fs_tree(
    project_id: str,
    path: str = Query(default="."),
    depth: int | None = Query(default=None, ge=1, le=100),
    user_id: PydanticObjectId = Depends(get_current_user_id),
) -> list[FileNode]:
    project = await _owned(project_id, user_id)
    return await WorkspaceService().tree(project, path, depth)


@router.get("/fs/file", response_model=FileContent)
async def fs_read(
    project_id: str,
    path: str = Query(..., min_length=1),
    user_id: PydanticObjectId = Depends(get_current_user_id),
) -> FileContent:
    project = await _owned(project_id, user_id)
    return await WorkspaceService().read(project, path)


@router.put("/fs/file", response_model=FileNode)
async def fs_write(
    project_id: str,
    body: WriteFileRequest,
    user_id: PydanticObjectId = Depends(get_current_user_id),
) -> FileNode:
    project = await _owned(project_id, user_id)
    return await WorkspaceService().write(project, body.path, body.content)


@router.delete("/fs/file", status_code=204)
async def fs_delete(
    project_id: str,
    path: str = Query(..., min_length=1),
    user_id: PydanticObjectId = Depends(get_current_user_id),
) -> None:
    project = await _owned(project_id, user_id)
    await WorkspaceService().delete(project, path)


@router.post("/fs/dir", response_model=FileNode)
async def fs_mkdir(
    project_id: str,
    body: MkdirRequest,
    user_id: PydanticObjectId = Depends(get_current_user_id),
) -> FileNode:
    project = await _owned(project_id, user_id)
    return await WorkspaceService().mkdir(project, body.path)


@router.post("/fs/move", response_model=FileNode)
async def fs_move(
    project_id: str,
    body: MoveRequest,
    user_id: PydanticObjectId = Depends(get_current_user_id),
) -> FileNode:
    project = await _owned(project_id, user_id)
    return await WorkspaceService().move(project, body.src, body.dst)


@router.post("/sandbox/git/commit", response_model=GitCommitResponse)
async def git_commit(
    project_id: str,
    body: GitCommitRequest,
    user_id: PydanticObjectId = Depends(get_current_user_id),
) -> GitCommitResponse:
    project = await _owned(project_id, user_id)
    sha, created = await WorkspaceService().commit(project, body.message)
    return GitCommitResponse(sha=sha, committed=created)


@router.get("/sandbox/git", response_model=GitInfo)
async def git_info(
    project_id: str,
    user_id: PydanticObjectId = Depends(get_current_user_id),
) -> GitInfo:
    project = await _owned(project_id, user_id)
    return await WorkspaceService().git_info(project)
