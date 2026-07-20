"""BYO credential API (phase-34).

Per-user, bring-your-own provider tokens. The response model is metadata-only by construction —
there is **no** endpoint that returns a stored secret, by design (§7). Platform-wide credentials
are not managed here: they come from config (or a platform-scoped vault row seeded out of band),
and platform *configuration* is a separate surface entirely (``PlatformSetting``, phase-51/52).
"""

from __future__ import annotations

from datetime import datetime

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.auth.deps import get_current_user_id
from app.core.errors import NotFoundError
from app.db.models.enums import CredentialKind, CredentialScope
from app.deploy.secrets import CredentialMeta, SecretVault

credentials_router = APIRouter(prefix="/credentials", tags=["credentials"])


class CredentialPublic(BaseModel):
    """The only shape a credential is ever exposed in — the value is never included."""

    kind: CredentialKind
    scope: CredentialScope
    created_at: datetime
    last4: str | None = None

    @classmethod
    def of(cls, meta: CredentialMeta) -> CredentialPublic:
        return cls(kind=meta.kind, scope=meta.scope, created_at=meta.created_at, last4=meta.last4)


class CredentialInput(BaseModel):
    """Write-only: the secret goes in and is never read back out."""

    secret: str = Field(min_length=1)


class DeleteResponse(BaseModel):
    deleted: bool


@credentials_router.get("", response_model=list[CredentialPublic])
async def list_credentials(
    user_id: PydanticObjectId = Depends(get_current_user_id),
) -> list[CredentialPublic]:
    """Metadata for the caller's stored credentials — kind/scope/created_at/last4 only."""
    return [CredentialPublic.of(m) for m in await SecretVault().list_kinds(user_id)]


@credentials_router.put("/{kind}", response_model=CredentialPublic)
async def put_credential(
    kind: CredentialKind,
    body: CredentialInput,
    user_id: PydanticObjectId = Depends(get_current_user_id),
) -> CredentialPublic:
    """Add or replace the caller's BYO token for ``kind`` (encrypted before it touches Mongo)."""
    meta = await SecretVault().put_credential(user_id, kind, body.secret, CredentialScope.byo)
    return CredentialPublic.of(meta)


@credentials_router.delete("/{kind}", response_model=DeleteResponse)
async def delete_credential(
    kind: CredentialKind,
    user_id: PydanticObjectId = Depends(get_current_user_id),
) -> DeleteResponse:
    removed = await SecretVault().delete(user_id, kind, CredentialScope.byo)
    if not removed:
        raise NotFoundError("No such credential")
    return DeleteResponse(deleted=True)
