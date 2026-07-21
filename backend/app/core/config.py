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
    # Agent client tuning (phase-20).
    anthropic_max_output_tokens: int = 4096
    anthropic_max_tool_turns: int = 16  # safety bound on the tool-use loop
    anthropic_max_retries: int = 4  # transient (429/5xx/network) backoff attempts
    # Per-model pricing (₹ per 1M tokens) as JSON: {"<model>": {"input": .., "output": ..}}.
    # Blank → the built-in defaults in app/agents/pricing.py. Admin-editable (phase-51/52).
    model_pricing_json: str = ""

    # --- Agent tools (phase-21) ---
    # Prebuilt generated-app skeleton copied into /workspace by instantiate_skeleton (phase-22/23).
    # Relative paths resolve from the repo root; absolute paths are used as-is.
    app_skeleton_dir: str = "templates/app-skeleton"
    tool_output_max_chars: int = 8000  # cap command output fed back to the model (keep the tail)

    # --- Codegen (phase-23) ---
    # Per-artifact cap on the design/requirements context fed to the codegen agent (cost discipline
    # — feed only what's needed, never the whole repo).
    codegen_context_char_budget: int = 6000

    # --- Test runner (phase-28) ---
    # Wall-clock bound for a single suite invocation in the sandbox (Playwright can be slow).
    test_runner_timeout_s: int = 900

    # --- Live validation (phase-39) ---
    # Which E2E subset runs against production: "smoke", "critical", "both", or "all" (everything).
    # Ignored when the suite carries no tags — an untagged suite runs in full rather than empty.
    live_test_selection: str = "both"
    live_test_timeout_s: int = 600  # wall clock for the live Playwright invocation
    # Render's free tier sleeps; the first request wakes it and can take ~a minute. Probe until the
    # site answers so results reflect real failures, not spin-up.
    live_warmup_attempts: int = 10
    live_warmup_interval_s: float = 6.0
    live_test_retries: int = 1  # Playwright's own retry for a flaky first hit over the network

    # --- MongoDB ---
    mongodb_uri: str = "mongodb://localhost:27017"
    forgeflow_meta_db: str = "forgeflow_meta"
    # Shared cluster that hosts generated apps' per-project databases in platform mode (phase-36,
    # D8). Blank → reuse `mongodb_uri` (fine for local dev). A per-project DB name is appended, so
    # this is a cluster base URI without a database path. Secret-ish (may carry Atlas creds).
    app_db_cluster_uri: str = ""

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
    workspace_max_file_bytes: int = 1_048_576  # per-file read/write cap for the FS API (phase-12)

    # --- Exec & terminal (phase-13) ---
    sandbox_exec_timeout_s: int = 600  # default per-command wall clock before the process is killed
    sandbox_max_concurrent_execs: int = 3  # per project
    sandbox_shell: str = "/bin/bash"  # interactive terminal shell inside the sandbox

    # --- Live preview (phase-15) ---
    # Commands run inside the sandbox against the phase-22 skeleton layout ({port} is substituted).
    preview_fe_dir: str = "frontend"
    preview_be_dir: str = "backend"
    preview_fe_cmd: str = "pnpm dev --host 0.0.0.0 --port {port}"
    preview_be_cmd: str = "pnpm dev"  # port arrives via the injected PORT env var
    preview_fe_port: int = 5173
    preview_be_port: int = 3001
    preview_health_timeout_s: int = 90  # how long start() waits for both servers to answer
    preview_health_path: str = "/"  # FE probe path; the BE probe uses /health (skeleton contract)
    preview_be_health_path: str = "/health"
    # Dedicated *internal* docker network for preview: sandbox↔proxy only, never the host (§7).
    preview_network_name: str = "forgeflow-preview"
    # Routed network the sandbox joins *only* for a live validation run (phase-39), then leaves.
    egress_network_name: str = "forgeflow-egress"
    preview_base_domain: str = (
        ""  # set in prod → https://<project>.<domain>; else *.preview.localhost
    )

    # --- Design providers (phase 17/18) ---
    stitch_gcp_project: str = ""
    stitch_client_id: str = ""
    stitch_client_secret: str = ""
    stitch_quota_monthly: int = 350
    # Stitch MCP + OAuth endpoints and call tuning (phase-17). Blank creds → provider health()=down
    # and the design stage falls back (fake/figma) rather than crashing.
    stitch_mcp_url: str = ""  # Stitch MCP server base URL
    stitch_token_url: str = ""  # OAuth2 token endpoint (client-credentials grant)
    stitch_scope: str = ""  # optional OAuth scope
    stitch_timeout_s: float = 60.0  # per-call wall clock
    stitch_max_retries: int = 2  # extra attempts on transient errors (total = 1 + this)
    stitch_token_skew_s: float = 60.0  # refresh this many seconds before real expiry
    stitch_quota_soft_ratio: float = 0.9  # used/limit >= this → health() degraded
    # Figma MCP (phase-18): a static access token (no OAuth refresh). Blank token → health()=down.
    figma_token: str = ""
    figma_mcp_url: str = ""  # Figma MCP server base URL
    figma_timeout_s: float = 60.0  # per-call wall clock
    figma_max_retries: int = 2  # extra attempts on transient errors
    design_provider: str = "stitch"
    # Design intake (phase-19): screenshot upload caps.
    design_max_images: int = 8
    design_max_image_bytes: int = 5_242_880  # 5 MiB per screenshot

    # --- Infra analyzer (phase-33) ---
    # The analyzer is static-analysis-first (cheap, deterministic, reproducible for eval), so the
    # only tuning it needs is how far it will walk a workspace looking for env references.
    infra_scan_max_files: int = 200
    infra_scan_max_depth: int = 6

    # --- Deploy providers (phase 34/35) ---
    # Platform-owned tokens. Per-user BYO tokens live in the encrypted vault (phase-34) and take
    # precedence; these are the seamless-mode fallback.
    vercel_token: str = ""
    render_api_key: str = ""
    deploy_default_mode: str = "seamless"
    # Adapter endpoints + call tuning (phase-35). Overridable so the eval harness and tests can
    # point at a stub without patching code.
    vercel_api_url: str = "https://api.vercel.com"
    render_api_url: str = "https://api.render.com"
    deploy_timeout_s: float = 60.0  # per-call wall clock
    deploy_max_retries: int = 2  # extra attempts on transient errors (total = 1 + this)
    # --- Deploy orchestration (phase-37) ---
    deploy_repo_url: str = ""  # git repo Render builds the backend from (blank locally/mocked)
    deploy_region: str = ""  # optional provider region for the backend service
    deploy_health_timeout_s: int = 180  # seconds to poll a deploy until it goes live

    # --- Repair loop (phase 29-31) ---
    repair_max_iterations: int = 5
    repair_stall_threshold: int = 2
    # Minimal-context budget (phase-29). The invariant is "never feed the whole repo when a diff
    # suffices" (§9), so the assembled context is hard-capped: oversized parts are trimmed by
    # priority (failure frequency) and what was dropped is recorded on the context.
    repair_context_max_chars: int = 24_000  # whole-context cap
    repair_context_max_file_chars: int = 8_000  # per-file cap before truncation
    repair_context_diff_max_chars: int = 8_000  # cap on the since-last-passing diff


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
