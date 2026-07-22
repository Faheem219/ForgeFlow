"""The catalog of admin-editable platform settings (phase-51).

The resolver (``config.py``) can override *any* ``Settings`` field from the DB layer, but not every
field should be exposed to an admin dashboard — some are secrets, some are deploy-env wiring.
This registry is the **allowlist**: only keys listed here may be written via the admin API, and each
carries the metadata the dashboard needs (category, type, description, whether it is sensitive or
needs a restart) plus the validation applied on write.

Two rules keep it safe:

- **Sensitive keys are read-only here.** API tokens (``anthropic_api_key`` …) are listed so the
  dashboard can *show* they exist, but they are managed through the encrypted vault (phase-34),
  never stored as a plain ``PlatformSetting``. A write to one is refused, pointing at the vault.
- **Every value is coerced + validated** to the field's type and range before it is stored, so the
  DB layer can never feed the resolver a value the code would choke on.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.core.config import Settings
from app.core.errors import UserError
from app.db.models.enums import SettingCategory

ValueType = str  # one of: "str" | "int" | "float" | "bool" | "enum" | "json"


@dataclass(frozen=True)
class ConfigKey:
    """Metadata + validation for one admin-editable setting."""

    key: str
    category: SettingCategory
    type: ValueType
    description: str
    sensitive: bool = False
    restart_required: bool = False
    #: Allowed values for ``type == "enum"``.
    choices: tuple[str, ...] = ()
    #: Inclusive numeric bounds for int/float.
    minimum: float | None = None
    maximum: float | None = None
    #: Whether a numeric value may be ``None`` (e.g. an "unset" budget cap).
    nullable: bool = False
    #: Extra, key-specific validation (raises ``UserError``). Runs after type coercion.
    extra: Callable[[Any], None] | None = field(default=None, compare=False)

    def default(self) -> Any:
        """The code default from ``Settings`` — the value in force when nothing overrides it."""
        return _defaults().get(self.key)


def _defaults() -> dict[str, Any]:
    # A pristine Settings() carries the code defaults (env is layered on by the resolver, not here).
    return Settings().model_dump()


def _positive(value: Any) -> None:
    if value is not None and value <= 0:
        raise UserError("must be greater than zero")


def _valid_json_object(value: Any) -> None:
    """``model_pricing_json`` must be blank or a JSON object of ``{model: {...}}``."""
    text = str(value).strip()
    if not text:
        return
    try:
        parsed = json.loads(text)
    except ValueError as exc:
        raise UserError(f"must be valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise UserError("must be a JSON object")


# The registry. Categories mirror ``SettingCategory``. Keys are ``Settings`` field names, so the DB
# layer overrides them by the same name the resolver reads.
_KEYS: tuple[ConfigKey, ...] = (
    # -- models --
    ConfigKey(
        "model_codegen", SettingCategory.models, "str", "Model id for real code/test/repair work."
    ),
    ConfigKey(
        "model_routing",
        SettingCategory.models,
        "str",
        "Cheap model for classification/summaries/routing.",
    ),
    ConfigKey(
        "anthropic_base_url",
        SettingCategory.models,
        "str",
        "Override the Anthropic API base URL (blank = SDK default).",
    ),
    ConfigKey(
        "anthropic_max_output_tokens",
        SettingCategory.models,
        "int",
        "Max output tokens per model call.",
        minimum=1,
        maximum=200_000,
    ),
    ConfigKey(
        "anthropic_api_key",
        SettingCategory.models,
        "str",
        "Anthropic API key — managed via the encrypted vault, not stored here.",
        sensitive=True,
    ),
    ConfigKey(
        "model_pricing_json",
        SettingCategory.budget,
        "json",
        "Per-model ₹/1M-token pricing as JSON, or blank for built-in defaults.",
        extra=_valid_json_object,
    ),
    # -- providers --
    ConfigKey(
        "design_provider",
        SettingCategory.providers,
        "enum",
        "Active design provider.",
        choices=("stitch", "figma", "fake"),
    ),
    ConfigKey(
        "design_fallback_chain",
        SettingCategory.providers,
        "str",
        "Comma-separated fallback order when the active provider fails (phase-48).",
    ),
    # -- deploy --
    ConfigKey(
        "deploy_default_mode",
        SettingCategory.deploy,
        "enum",
        "Default deploy credential mode.",
        choices=("seamless", "byo"),
    ),
    # -- sandbox (apply to newly-created sandboxes; existing ones must be recreated) --
    ConfigKey(
        "sandbox_cpu_limit",
        SettingCategory.sandbox,
        "float",
        "CPU cores per sandbox.",
        restart_required=True,
        minimum=0.1,
        maximum=16,
        extra=_positive,
    ),
    ConfigKey(
        "sandbox_mem_limit",
        SettingCategory.sandbox,
        "str",
        "Memory per sandbox (e.g. '1g', '512m').",
        restart_required=True,
    ),
    ConfigKey(
        "sandbox_pids_limit",
        SettingCategory.sandbox,
        "int",
        "Max processes per sandbox (fork-bomb guard).",
        restart_required=True,
        minimum=16,
        maximum=4096,
    ),
    ConfigKey(
        "sandbox_idle_timeout_s",
        SettingCategory.sandbox,
        "int",
        "Idle seconds before a sandbox is reaped.",
        minimum=30,
        maximum=86_400,
    ),
    # -- budget --
    ConfigKey(
        "budget_cap_inr_per_project",
        SettingCategory.budget,
        "float",
        "Per-project spend cap in ₹ (blank = uncapped).",
        nullable=True,
        extra=_positive,
    ),
    ConfigKey(
        "budget_cap_inr_global",
        SettingCategory.budget,
        "float",
        "Platform-wide spend cap in ₹ (blank = uncapped).",
        nullable=True,
        extra=_positive,
    ),
    # -- repair --
    ConfigKey(
        "repair_max_iterations",
        SettingCategory.repair,
        "int",
        "Hard cap on repair-loop iterations.",
        minimum=1,
        maximum=20,
    ),
    ConfigKey(
        "repair_stall_threshold",
        SettingCategory.repair,
        "int",
        "No-progress iterations before the loop escalates.",
        minimum=1,
        maximum=10,
    ),
    # -- feature flags --
    ConfigKey(
        "log_json",
        SettingCategory.feature_flags,
        "bool",
        "Emit structured JSON logs.",
        restart_required=True,
    ),
)

REGISTRY: dict[str, ConfigKey] = {k.key: k for k in _KEYS}


def registry() -> dict[str, ConfigKey]:
    return REGISTRY


def get_registered(key: str) -> ConfigKey:
    """The registry entry for ``key``, or a ``UserError`` naming it as not admin-editable."""
    entry = REGISTRY.get(key)
    if entry is None:
        raise UserError(f"{key!r} is not an admin-editable setting", detail={"key": key})
    return entry


def coerce_and_validate(key: str, raw: Any) -> Any:
    """Type-coerce + validate a raw admin input into the value that will be stored.

    Raises ``UserError`` (surfaced as 400 with detail) on any type/range/enum/sensitive violation.
    The returned value is already the correct Python type, so the resolver serves it without
    re-coercion.
    """
    entry = get_registered(key)
    if entry.sensitive:
        raise UserError(
            f"{key!r} is a secret — set it in the credentials vault, not platform config",
            detail={"key": key, "sensitive": True},
        )

    value = _coerce(entry, raw)
    if entry.type in ("int", "float") and value is not None:
        _check_range(entry, value)
    if entry.type == "enum" and value not in entry.choices:
        raise UserError(
            f"{key!r} must be one of {', '.join(entry.choices)}",
            detail={"key": key, "choices": list(entry.choices)},
        )
    if entry.extra is not None:
        try:
            entry.extra(value)
        except UserError as exc:
            raise UserError(f"{key!r} {exc.message}", detail={"key": key}) from exc
    return value


def _coerce(entry: ConfigKey, raw: Any) -> Any:
    if raw is None:
        if entry.nullable:
            return None
        raise UserError(f"{entry.key!r} must not be empty", detail={"key": entry.key})

    try:
        if entry.type == "int":
            return int(raw)
        if entry.type == "float":
            return float(raw)
        if entry.type == "bool":
            return _to_bool(raw)
        # str / enum / json are all stored as strings.
        return str(raw)
    except (TypeError, ValueError) as exc:
        raise UserError(
            f"{entry.key!r} must be a valid {entry.type}", detail={"key": entry.key}
        ) from exc


def _to_bool(raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        lowered = raw.strip().lower()
        if lowered in ("true", "1", "yes", "on"):
            return True
        if lowered in ("false", "0", "no", "off"):
            return False
    raise ValueError("not a boolean")


def _check_range(entry: ConfigKey, value: float) -> None:
    if entry.minimum is not None and value < entry.minimum:
        raise UserError(f"{entry.key!r} must be ≥ {entry.minimum}", detail={"key": entry.key})
    if entry.maximum is not None and value > entry.maximum:
        raise UserError(f"{entry.key!r} must be ≤ {entry.maximum}", detail={"key": entry.key})


__all__ = [
    "REGISTRY",
    "ConfigKey",
    "coerce_and_validate",
    "get_registered",
    "registry",
]
