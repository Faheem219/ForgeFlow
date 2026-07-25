"""Admin config service (phase-51) — read the catalog, write/revert overrides, audit every change.

Sits between the admin API and the two config layers it touches: it writes/deletes
``PlatformSetting`` rows, then refreshes the DB provider (``config_db``) so the change is live
immediately, and records a ``ConfigAudit`` entry for accountability. Reads report each key's
*effective* value and *source* (``db | env | default``).

**Secrets** (registry ``sensitive`` keys) may be *set* here but never *read* back: the value is
Fernet-encrypted before it is stored, the view returns only a mask, and the audit records a mask on
both sides of the change. Plaintext is produced only by ``config_db`` at read time, for the duration
of the call that needs it (§7).
"""

from __future__ import annotations

from typing import Any

from beanie import PydanticObjectId
from pydantic import BaseModel, Field

from app.core.config import get_config
from app.core.config_db import get_db_provider
from app.core.config_registry import REGISTRY, ConfigKey, coerce_and_validate, get_registered
from app.core.errors import UserError
from app.db.models import ConfigAudit
from app.db.repos import PlatformSettingRepo

MASK = "••••••"

# Cap on the audit read (the dashboard shows recent history, not the full log).
_AUDIT_LIMIT = 200

# Tokens that should not simply be title-cased when a key is turned into a human label.
_WORDS: dict[str, str] = {
    "anthropic": "Anthropic",
    "api": "API",
    "be": "backend",
    "cmd": "command",
    "cors": "CORS",
    "cpu": "CPU",
    "db": "DB",
    "dir": "directory",
    "e2e": "E2E",
    "fe": "frontend",
    "figma": "Figma",
    "forgeflow": "ForgeFlow",
    "fs": "filesystem",
    "gcp": "GCP",
    "id": "ID",
    "inr": "₹",
    "jwt": "JWT",
    "json": "JSON",
    "llm": "LLM",
    "mcp": "MCP",
    "mem": "memory",
    "mongodb": "MongoDB",
    "openai": "OpenAI",
    "pids": "PID",
    "render": "Render",
    "stitch": "Stitch",
    "uri": "URI",
    "url": "URL",
    "vercel": "Vercel",
}


def _label(key: str) -> str:
    """A human label for a settings key: ``openai_base_url`` → ``OpenAI base URL``."""
    tokens = key.split("_")
    suffix = ""
    if tokens[-1] == "s" and len(tokens) > 1:  # `*_timeout_s` → "(seconds)"
        tokens = tokens[:-1]
        suffix = " (seconds)"
    words = [_WORDS.get(t, t) for t in tokens]
    head, *rest = words
    if head == tokens[0]:  # not a special token — capitalise it
        head = head.capitalize()
    return " ".join([head, *rest]) + suffix


class SettingView(BaseModel):
    """What the dashboard shows for one key — never a raw secret."""

    key: str
    label: str
    env_var: str  # the environment variable this key falls back to
    category: str
    type: str
    description: str
    sensitive: bool
    restart_required: bool
    locked: bool = False
    locked_reason: str = ""
    choices: list[str] = Field(default_factory=list)
    minimum: float | None = None
    maximum: float | None = None
    nullable: bool = False
    value: Any = None  # effective value, masked when sensitive
    default: Any = None  # the code default (what env itself falls back to)
    source: str  # db | env | default


class ConfigAuditView(BaseModel):
    """One audited config change for the admin audit view (phase-52). Secrets stay masked."""

    key: str
    action: str  # update | delete
    before: Any = None
    after: Any = None
    updated_by: str | None = None
    created_at: str


class BulkResult(BaseModel):
    """Outcome of a multi-key save: what landed, and why anything else did not."""

    updated: list[SettingView] = Field(default_factory=list)
    errors: dict[str, str] = Field(default_factory=dict)


def _mask(entry: ConfigKey, value: Any) -> Any:
    if not entry.sensitive:
        return value
    return MASK if value not in (None, "") else ""


def _mask_audit(key: str, value: Any) -> Any:
    """Never surface a value for a sensitive key in the audit view, even historically."""
    entry = REGISTRY.get(key)
    if entry is not None and entry.sensitive and value not in (None, ""):
        return MASK
    return value


def _effective(entry: ConfigKey) -> tuple[Any, str]:
    """The value to display and its source.

    For a secret this deliberately avoids resolving the plaintext at all: it reports *whether* a
    value is configured and *where* it comes from, and nothing more.
    """
    config = get_config()
    if not entry.sensitive:
        return config.get(entry.key), config.source_of(entry.key)

    provider = get_db_provider()
    if provider is not None and provider.is_overridden(entry.key):
        return MASK, "db"
    raw = getattr(config.settings, entry.key, "")
    return (MASK if str(raw).strip() else ""), config.source_of(entry.key)


def _view(entry: ConfigKey) -> SettingView:
    value, source = _effective(entry)
    return SettingView(
        key=entry.key,
        label=_label(entry.key),
        env_var=entry.env_var,
        category=str(entry.category),
        type=entry.type,
        description=entry.description,
        sensitive=entry.sensitive,
        restart_required=entry.restart_required,
        locked=entry.locked,
        locked_reason=entry.locked_reason,
        choices=list(entry.choices),
        minimum=entry.minimum,
        maximum=entry.maximum,
        nullable=entry.nullable,
        value=value,
        default=_mask(entry, entry.default()),
        source=source,
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
        return {entry.key: _effective(entry)[0] for entry in REGISTRY.values()}

    async def get_one(self, key: str) -> SettingView:
        await self.ensure_fresh()
        return _view(get_registered(key))

    async def set(self, key: str, raw: Any, admin_id: PydanticObjectId | None) -> SettingView:
        """Validate, store, refresh, audit. Precedence makes this override env immediately."""
        entry = get_registered(key)
        value = coerce_and_validate(key, raw)  # raises UserError on bad input / locked key

        before, _ = _effective(entry)  # already masked for secrets
        stored, encrypted = self._prepare(entry, value)
        await self._settings.upsert(
            key, stored, entry.category, updated_by=admin_id, encrypted=encrypted
        )
        await self._refresh()
        after = MASK if entry.sensitive else value
        await _audit(key, "update", before, after, admin_id)
        return _view(entry)

    async def set_many(
        self, updates: dict[str, Any], admin_id: PydanticObjectId | None
    ) -> BulkResult:
        """Apply several writes, reporting per-key failures instead of failing the whole save.

        A section of the dashboard can hold dozens of edits; one bad value must not silently discard
        the rest, and the operator needs to know exactly which key was rejected.
        """
        result = BulkResult()
        for key, raw in updates.items():
            try:
                result.updated.append(await self.set(key, raw, admin_id))
            except UserError as exc:
                result.errors[key] = exc.message
        return result

    @staticmethod
    def _prepare(entry: ConfigKey, value: Any) -> tuple[Any, bool]:
        """The value as it will be persisted: ciphertext for a secret, plain otherwise."""
        if not entry.sensitive:
            return value, False
        from app.deploy.secrets import SecretVault  # local: the vault imports the DB models

        return SecretVault().encrypt(str(value)), True

    async def list_audit(self, limit: int = _AUDIT_LIMIT) -> list[ConfigAuditView]:
        """Recent config changes, newest first. Sensitive keys are masked defensively (a secret is
        already stored masked in the audit, but the view never risks surfacing one either)."""
        docs = await ConfigAudit.find().sort("-created_at").limit(limit).to_list()
        return [
            ConfigAuditView(
                key=doc.key,
                action=doc.action,
                before=_mask_audit(doc.key, doc.before),
                after=_mask_audit(doc.key, doc.after),
                updated_by=str(doc.updated_by) if doc.updated_by is not None else None,
                created_at=doc.created_at.isoformat(),
            )
            for doc in docs
        ]

    async def delete(self, key: str, admin_id: PydanticObjectId | None) -> SettingView:
        """Remove the override → the key reverts to env/default."""
        entry = get_registered(key)
        before, _ = _effective(entry)
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


__all__ = ["BulkResult", "ConfigAdminService", "ConfigAuditView", "SettingView"]
