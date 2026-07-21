"""Data browser API (phase-41) — CRUD over the *owning project's* database.

Every route resolves the project through ``get_owned`` first, and the service derives the database
from that project. No route accepts a database name, so there is no request shape that can address
another project's data or the control plane.

- ``GET    /projects/{id}/data/info`` — where this project's data lives (mode/db name; never a URI)
- ``GET    /projects/{id}/data/collections`` — collections + document counts
- ``GET    /projects/{id}/data/collections/{c}/docs`` — filter/sort/paged documents
- ``POST   /projects/{id}/data/collections/{c}/docs`` — insert
- ``GET    /projects/{id}/data/collections/{c}/docs/{doc_id}`` — one document
- ``PUT    /projects/{id}/data/collections/{c}/docs/{doc_id}`` — update fields
- ``DELETE /projects/{id}/data/collections/{c}/docs/{doc_id}`` — delete (**irreversible**)
"""

from __future__ import annotations

import json
from typing import Any

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.auth.deps import get_current_user_id
from app.core.errors import UserError
from app.data_browser.service import DataBrowserService
from app.projects.service import ProjectService, parse_object_id

data_router = APIRouter(prefix="/projects", tags=["data"])


class DbInfoPublic(BaseModel):
    """Where the project's data lives. Deliberately no URI field — it is a secret (§7)."""

    mode: str
    db_name: str
    managed: bool


class CollectionPublic(BaseModel):
    name: str
    count: int


class DocumentPagePublic(BaseModel):
    documents: list[dict[str, Any]] = Field(default_factory=list)
    total: int
    page: int
    limit: int
    pages: int


class DocumentBody(BaseModel):
    document: dict[str, Any]


class DeletedPublic(BaseModel):
    deleted: bool


def _parse_json_param(raw: str | None, label: str) -> Any:
    """Query params arrive as JSON strings; a malformed one is the user's error, not a 500."""
    if raw is None or raw.strip() == "":
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        raise UserError(f"{label} must be valid JSON") from None


async def _project(project_id: str, user_id: PydanticObjectId) -> Any:
    return await ProjectService().get_owned(parse_object_id(project_id), user_id)


@data_router.get("/{project_id}/data/info", response_model=DbInfoPublic)
async def db_info(
    project_id: str, user_id: PydanticObjectId = Depends(get_current_user_id)
) -> DbInfoPublic:
    """Secret-safe description of the project's database (mode, name, whether ForgeFlow owns it)."""
    project = await _project(project_id, user_id)
    info = await DataBrowserService().db_info(project)
    return DbInfoPublic(mode=str(info.mode), db_name=info.db_name, managed=info.managed)


@data_router.get("/{project_id}/data/collections", response_model=list[CollectionPublic])
async def list_collections(
    project_id: str, user_id: PydanticObjectId = Depends(get_current_user_id)
) -> list[CollectionPublic]:
    project = await _project(project_id, user_id)
    collections = await DataBrowserService().list_collections(project)
    return [CollectionPublic(name=c.name, count=c.count) for c in collections]


@data_router.get(
    "/{project_id}/data/collections/{collection}/docs", response_model=DocumentPagePublic
)
async def list_documents(
    project_id: str,
    collection: str,
    filter: str | None = Query(default=None, description="JSON filter"),  # noqa: A002
    sort: str | None = Query(default=None, description='JSON sort, e.g. {"createdAt": -1}'),
    page: int = Query(default=1, ge=1),
    limit: int | None = Query(default=None, ge=1),
    user_id: PydanticObjectId = Depends(get_current_user_id),
) -> DocumentPagePublic:
    project = await _project(project_id, user_id)
    result = await DataBrowserService().find(
        project,
        collection,
        filter=_parse_json_param(filter, "Filter"),
        sort=_parse_json_param(sort, "Sort"),
        page=page,
        limit=limit,
    )
    return DocumentPagePublic(
        documents=result.documents,
        total=result.total,
        page=result.page,
        limit=result.limit,
        pages=result.pages,
    )


@data_router.post(
    "/{project_id}/data/collections/{collection}/docs",
    response_model=dict[str, Any],
    status_code=201,
)
async def create_document(
    project_id: str,
    collection: str,
    body: DocumentBody,
    user_id: PydanticObjectId = Depends(get_current_user_id),
) -> dict[str, Any]:
    project = await _project(project_id, user_id)
    return await DataBrowserService().insert(project, collection, body.document)


@data_router.get(
    "/{project_id}/data/collections/{collection}/docs/{doc_id}", response_model=dict[str, Any]
)
async def get_document(
    project_id: str,
    collection: str,
    doc_id: str,
    user_id: PydanticObjectId = Depends(get_current_user_id),
) -> dict[str, Any]:
    project = await _project(project_id, user_id)
    return await DataBrowserService().get(project, collection, doc_id)


@data_router.put(
    "/{project_id}/data/collections/{collection}/docs/{doc_id}", response_model=dict[str, Any]
)
async def update_document(
    project_id: str,
    collection: str,
    doc_id: str,
    body: DocumentBody,
    user_id: PydanticObjectId = Depends(get_current_user_id),
) -> dict[str, Any]:
    project = await _project(project_id, user_id)
    return await DataBrowserService().update(project, collection, doc_id, body.document)


@data_router.delete(
    "/{project_id}/data/collections/{collection}/docs/{doc_id}", response_model=DeletedPublic
)
async def delete_document(
    project_id: str,
    collection: str,
    doc_id: str,
    user_id: PydanticObjectId = Depends(get_current_user_id),
) -> DeletedPublic:
    """Delete a document. **Irreversible** — there are no app-data backups by default."""
    project = await _project(project_id, user_id)
    deleted = await DataBrowserService().delete(project, collection, doc_id)
    return DeletedPublic(deleted=deleted)
