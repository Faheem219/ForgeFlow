"""Layered configuration resolver.

Resolution order (highest priority first):

    1. registered override providers  — phase-51 prepends a DB/admin provider
    2. environment / ``.env``          — via pydantic-settings on ``Settings``
    3. code defaults                   — field defaults on ``Settings``

i.e. precedence is **admin panel (DB) > env > default**, exactly the user directive.

All application code MUST read configuration through :func:`get_config` (never ``os.environ``
directly — enforced by ``infra/scripts/check_no_os_environ.sh``). This module is the single
allowed place to touch the environment.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any, Protocol, TypeVar, runtime_checkable

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.errors import SystemError

T = TypeVar("T")


class _Missing:
    """Sentinel meaning 'this provider does not supply this key'."""

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return "MISSING"


MISSING: Any = _Missing()


class Settings(BaseSettings):
    """Typed env + default snapshot. Every key is documented in ``.env.example``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Core ---
    forgeflow_env: str = "local"
    secret_key: str = "dev-insecure-change-me"
    fernet_key: str = ""
    app_version: str = "0.1.0"
    cors_origins: str = "http://localhost:5173,http://localhost"
    log_level: str = "INFO"
    log_json: bool = True

    # --- Auth (phase-03) ---
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 1440
    auth_rate_limit_per_minute: int = 30  # per client IP on /auth/login + /auth/register
    seed_admin_email: str = ""  # optional dev admin, created by scripts/seed.py when both set
    seed_admin_password: str = ""

    # --- Realtime (phase-05) ---
    realtime_ring_size: int = 256  # per-project replay ring buffer size
    realtime_heartbeat_s: float = 20.0  # WS heartbeat/ping interval

    # --- Anthropic ---
    anthropic_api_key: str = ""
    anthropic_base_url: str = ""
    model_codegen: str = "claude-sonnet-5"
    model_routing: str = "claude-haiku-4-5-20251001"
    budget_cap_inr_per_project: float | None = None
    budget_cap_inr_global: float | None = None

    # --- MongoDB ---
    mongodb_uri: str = "mongodb://localhost:27017"
    forgeflow_meta_db: str = "forgeflow_meta"

    # --- Artifacts & blobs (phase-07) ---
    blob_backend: str = "gridfs"  # gridfs | filesystem
    blob_fs_dir: str = ""  # filesystem-backend root (required when blob_backend=filesystem)
    artifact_inline_max_bytes: int = 16384  # payloads <= this stay inline; larger go to a blob

    # --- Sandbox (phase 10/11) ---
    sandbox_image: str = "forgeflow-sandbox:latest"
    sandbox_cpu_limit: float = 1.0
    sandbox_mem_limit: str = "1g"
    sandbox_pids_limit: int = 256
    sandbox_idle_timeout_s: int = 900
    sandbox_read_only_root: bool = True  # read-only root; /workspace volume + /tmp stay writable
    sandbox_reap_interval_s: int = 60  # how often the idle reaper sweeps
    preview_base_domain: str = ""

    # --- Design providers (phase 17/18) ---
    stitch_gcp_project: str = ""
    stitch_client_id: str = ""
    stitch_client_secret: str = ""
    stitch_quota_monthly: int = 350
    figma_token: str = ""
    design_provider: str = "stitch"

    # --- Deploy providers (phase 34/35) ---
    vercel_token: str = ""
    render_api_key: str = ""
    deploy_default_mode: str = "seamless"

    # --- Repair loop (phase 29-31) ---
    repair_max_iterations: int = 5
    repair_stall_threshold: int = 2


@runtime_checkable
class SettingProvider(Protocol):
    """An override source. Higher-priority providers are consulted first.

    ``get`` returns the override value for ``key``, or :data:`MISSING` if it does not supply it.
    """

    name: str

    def get(self, key: str) -> Any: ...


class ConfigResolver:
    """Resolve settings across the provider chain, then env, then defaults."""

    def __init__(
        self,
        settings: Settings | None = None,
        providers: list[SettingProvider] | None = None,
    ) -> None:
        self._settings = settings if settings is not None else Settings()
        self._providers: list[SettingProvider] = list(providers or [])

    @property
    def settings(self) -> Settings:
        """The typed env/default snapshot (does not include override providers)."""
        return self._settings

    def add_provider(self, provider: SettingProvider) -> None:
        """Register a higher-priority override source. Used by phase-51 (DB/admin layer)."""
        self._providers.insert(0, provider)

    def reload(self) -> None:
        """Rebuild the env/default snapshot (called after admin writes, phase-51)."""
        self._settings = Settings()

    def _ensure_known(self, key: str) -> None:
        if key not in type(self._settings).model_fields:
            raise SystemError(f"Unknown config key: {key!r}")

    def get(self, key: str, cast: Callable[[Any], T] | None = None) -> Any:
        """Return the effective value for ``key`` honoring provider > env > default."""
        self._ensure_known(key)
        for provider in self._providers:
            value = provider.get(key)
            if value is not MISSING:
                return cast(value) if cast is not None else value
        value = getattr(self._settings, key)
        return cast(value) if cast is not None else value

    def source_of(self, key: str) -> str:
        """Report where ``key`` resolves from: ``<provider name>`` | ``'env'`` | ``'default'``."""
        self._ensure_known(key)
        for provider in self._providers:
            if provider.get(key) is not MISSING:
                return provider.name
        if key.upper() in os.environ or key.lower() in os.environ:
            return "env"
        return "default"


_resolver: ConfigResolver | None = None


def get_config() -> ConfigResolver:
    """Return the process-wide config resolver (built once)."""
    global _resolver
    if _resolver is None:
        _resolver = ConfigResolver()
    return _resolver


def reset_config() -> None:
    """Drop the cached resolver (test helper / after major env changes)."""
    global _resolver
    _resolver = None
