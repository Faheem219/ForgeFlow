"""Admin config service (phase-51) — read the catalog, write/revert overrides, audit every change.

Sits between the admin API and the two config layers it touches: it writes/deletes
``PlatformSetting`` rows, then refreshes the DB provider (``config_db``) so the change is live
immediately, and records a ``ConfigAudit`` entry for accountability. Reads report each key's
*effective* value and *source* (``db | env | default``) with sensitive values masked.
"""

from __future__ import annotations

from typing import Any

from beanie import PydanticObjectId
from pydantic import BaseModel, Field

from app.core.config import get_config
from app.core.config_db import get_db_provider
from app.core.config_registry import REGISTRY, ConfigKey, coerce_and_validate, get_registered
from app.db.models import ConfigAudit
from app.db.repos import PlatformSettingRepo

MASK = "••••••"


class SettingView(BaseModel):
    """What the dashboard shows for one key — never a raw secret."""

    key: str
    category: str
    type: str
    description: str
    sensitive: bool
    restart_required: bool
    choices: list[str] = Field(default_factory=list)
    value: Any = None  # effective value, masked when sensitive
    default: Any = None
    source: str  # db | env | default


def _mask(entry: ConfigKey, value: Any) -> Any:
    if not entry.sensitive:
        return value
    return MASK if value not in (None, "") else ""


def _view(entry: ConfigKey) -> SettingView:
    config = get_config()
    effective = config.get(entry.key)
    return SettingView(
        key=entry.key,
        category=str(entry.category),
        type=entry.type,
        description=entry.description,
        sensitive=entry.sensitive,
        restart_required=entry.restart_required,
        choices=list(entry.choices),
        value=_mask(entry, effective),
        default=_mask(entry, entry.default()),
        source=config.source_of(entry.key),
    )


class ConfigAdminService:
    def __init__(self) -> None:
        self._settings = PlatformSettingRepo()

    async def _refresh(self) -> None:
        """Make a just-written change live in the sync resolver cache (no restart)."""
        provider = get_db_provider()
        if provider is not None:
            await provider.load()

    async def ensure_fresh(self) -> None:
        provider = get_db_provider()
        if provider is not None:
            await provider.ensure_fresh()

    async def list_settings(self) -> list[SettingView]:
        await self.ensure_fresh()
        return [_view(entry) for entry in REGISTRY.values()]

    async def effective(self) -> dict[str, Any]:
        """A flat ``{key: effective_value}`` snapshot, sensitive values masked."""
        await self.ensure_fresh()
        config = get_config()
        return {entry.key: _mask(entry, config.get(entry.key)) for entry in REGISTRY.values()}

    async def get_one(self, key: str) -> SettingView:
        await self.ensure_fresh()
        return _view(get_registered(key))

    async def set(self, key: str, raw: Any, admin_id: PydanticObjectId | None) -> SettingView:
        """Validate, store, refresh, audit. Precedence makes this override env immediately."""
        entry = get_registered(key)
        value = coerce_and_validate(key, raw)  # raises UserError on bad input / sensitive

        before = get_config().get(key)  # effective value before the change (for the audit)
        await self._settings.upsert(key, value, entry.category, updated_by=admin_id)
        await self._refresh()
        await _audit(key, "update", before, value, admin_id)
        return _view(entry)

    async def delete(self, key: str, admin_id: PydanticObjectId | None) -> SettingView:
        """Remove the override → the key reverts to env/default."""
        entry = get_registered(key)
        before = get_config().get(key)
        existing = await self._settings.get_by_key(key)
        if existing is not None:
            await existing.delete()
        await self._refresh()
        await _audit(key, "delete", before, None, admin_id)
        return _view(entry)


async def _audit(
    key: str, action: str, before: Any, after: Any, admin_id: PydanticObjectId | None
) -> None:
    await ConfigAudit(
        key=key, action=action, before=before, after=after, updated_by=admin_id
    ).insert()


__all__ = ["ConfigAdminService", "SettingView"]
