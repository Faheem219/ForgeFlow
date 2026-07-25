"""Admin platform-config API (phase-51). Every route is ``require_admin``.

    GET    /admin/config             catalog + effective value + source per key (sensitive masked)
    GET    /admin/config/effective   flat {key: value} snapshot
    GET    /admin/config/audit       recent changes (who/what/when, before→after)
    GET    /admin/config/{key}        one key's view
    PUT    /admin/config             bulk save: {"updates": {key: value}} → per-key results
    PUT    /admin/config/{key}        validate → upsert → refresh (no restart) → audit
    DELETE /admin/config/{key}        revert to env/default → audit

Writes go through :class:`~app.core.config_admin.ConfigAdminService`, which enforces the registry
allowlist, type/range/enum validation, secret encryption, locked-key protection, and the audit
trail. Precedence is ``admin(DB) > env > default``, so a write takes effect immediately.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.auth.deps import require_admin
from app.core.config_admin import BulkResult, ConfigAdminService, ConfigAuditView, SettingView
from app.db.models import User

config_router = APIRouter(prefix="/admin/config", tags=["admin-config"])


class SettingWrite(BaseModel):
    #: The new value; the service coerces it to the key's type. ``null`` allowed only for
    #: nullable keys (e.g. an "unset" budget cap).
    value: Any = None


class BulkWrite(BaseModel):
    #: ``{key: value}`` for every edited key. Each is validated independently so one bad value
    #: rejects only itself.
    updates: dict[str, Any] = Field(default_factory=dict)


class DeleteResponse(BaseModel):
    reverted: SettingView


@config_router.get("", response_model=list[SettingView])
async def list_config(_admin: User = Depends(require_admin)) -> list[SettingView]:
    return await ConfigAdminService().list_settings()


@config_router.get("/effective", response_model=dict[str, Any])
async def effective_config(_admin: User = Depends(require_admin)) -> dict[str, Any]:
    return await ConfigAdminService().effective()


# Declared before ``/{key}`` so "audit" is a literal route, not captured as a config key.
@config_router.get("/audit", response_model=list[ConfigAuditView])
async def config_audit(_admin: User = Depends(require_admin)) -> list[ConfigAuditView]:
    """Recent config changes (who/what/when, before→after) for the admin audit view."""
    return await ConfigAdminService().list_audit()


@config_router.get("/{key}", response_model=SettingView)
async def get_config_key(key: str, _admin: User = Depends(require_admin)) -> SettingView:
    return await ConfigAdminService().get_one(key)


@config_router.put("", response_model=BulkResult)
async def put_config_bulk(body: BulkWrite, admin: User = Depends(require_admin)) -> BulkResult:
    """Save a whole section at once; rejected keys come back in ``errors`` with their reason."""
    return await ConfigAdminService().set_many(body.updates, admin.id)


@config_router.put("/{key}", response_model=SettingView)
async def put_config_key(
    key: str, body: SettingWrite, admin: User = Depends(require_admin)
) -> SettingView:
    return await ConfigAdminService().set(key, body.value, admin.id)


@config_router.delete("/{key}", response_model=DeleteResponse)
async def delete_config_key(key: str, admin: User = Depends(require_admin)) -> DeleteResponse:
    reverted = await ConfigAdminService().delete(key, admin.id)
    return DeleteResponse(reverted=reverted)
