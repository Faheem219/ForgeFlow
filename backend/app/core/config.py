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
from collections.abc import Callable, Mapping
from typing import Any, Protocol, TypeVar, runtime_checkable

from pydantic import field_validator
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
    # --- Metrics (devops) ---
    # Prometheus scrape endpoint. Disable to stop exporting entirely; the middleware is then
    # never installed, so there is no measurement overhead either.
    metrics_enabled: bool = True
    metrics_path: str = "/metrics"

    # --- Auth (phase-03) ---
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 1440
    auth_rate_limit_per_minute: int = 30  # per client IP on /auth/login + /auth/register
    # Per-user cap on endpoints that cost money or capacity — model calls, sandbox work, deploys
    # (phase-47). 0 disables. Keyed by user, not IP, so cost lands on whoever incurred it.
    expensive_rate_limit_per_minute: int = 20
    # Hard backstop on any single request body (phase-47). Per-route caps stay tighter; this
    # exists so no request can exhaust process memory. Must exceed the design-image batch
    # (design_max_images * design_max_image_bytes).
    max_request_bytes: int = 67_108_864  # 64 MiB
    seed_admin_email: str = ""  # optional dev admin, created by scripts/seed.py when both set
    seed_admin_password: str = ""
    # Demo account seeded by scripts/demo.py (phase-50). Password blank → the script refuses to
    # seed rather than shipping a known credential; set it explicitly for a rehearsal.
    demo_email: str = "demo@forgeflow.dev"
    demo_password: str = ""

    # --- Realtime (phase-05) ---
    realtime_ring_size: int = 256  # per-project replay ring buffer size
    realtime_heartbeat_s: float = 20.0  # WS heartbeat/ping interval

    # --- Anthropic ---
    anthropic_api_key: str = ""
    anthropic_base_url: str = ""
    model_codegen: str = "claude-sonnet-5"
    model_routing: str = "claude-haiku-4-5-20251001"
    # Model for cheap CLASSIFICATION calls (phase-60). Blank -> use model_routing. Split out
    # because it is the highest-volume cheap call and often wants a different model.
    model_classify: str = ""
    budget_cap_inr_per_project: float | None = None
    budget_cap_inr_global: float | None = None

    @field_validator("budget_cap_inr_per_project", "budget_cap_inr_global", mode="before")
    @classmethod
    def _blank_cap_means_uncapped(cls, value: object) -> object:
        """A blank cap means *uncapped*, not an error. `.env.example` ships these blank, and
        docker-compose's `env_file` passes a blank key through as an empty-string env var (it does
        not omit it) — so `float | None` would otherwise choke on `""`. Only whitespace/empty is
        remapped; a real non-numeric value (e.g. a stray inline comment) still fails loudly."""
        if isinstance(value, str) and value.strip() == "":
            return None
        return value

    # Agent client tuning (phase-20). The output cap was raised 4096 → 16384 in phase-64: a
    # reasoning model spends part of it *thinking*, and a cap that holds a thought but not the
    # tool call after it is how a build phase came to write nothing.
    anthropic_max_output_tokens: int = 16384
    # Safety bound on the tool-use loop. A real full-stack build writes a file per tool turn and
    # then installs/boots/tests, so a low bound aborts large projects mid-build; the loop stays
    # bounded (D7) and every turn is still budget-checked.
    anthropic_max_tool_turns: int = 250
    anthropic_max_retries: int = 4  # transient (429/5xx/network) backoff attempts
    # Per-model pricing (₹ per 1M tokens) as JSON: {"<model>": {"input": .., "output": ..}}.
    # Blank → the built-in defaults in app/agents/pricing.py. Admin-editable (phase-51/52).
    model_pricing_json: str = ""

    # --- LLM provider (phase-53) ---
    # Which chat backend the agents talk to: "anthropic" (default, Claude Messages API) or "openai"
    # (any OpenAI-*compatible* Chat Completions endpoint — OpenAI, Azure OpenAI, Together, Groq,
    # OpenRouter, vLLM, Ollama, …). The agent loop, tools, routing and cost accounting are
    # provider-agnostic; only the transport differs (app/agents/anthropic_client.py). When set to
    # "openai", point MODEL_CODEGEN / MODEL_ROUTING at that provider's model ids and price them via
    # MODEL_PRICING_JSON.
    llm_provider: str = "anthropic"
    openai_api_key: str = ""
    # Blank → the OpenAI SDK default (https://api.openai.com/v1). Set to any compatible base URL,
    # e.g. http://localhost:11434/v1 (Ollama) or https://openrouter.ai/api/v1.
    # When (and only when) this points at OpenRouter, the transport adds OpenRouter's `provider`
    # routing preference to sort upstreams by price — see build_openai_params in
    # app/agents/anthropic_client.py. Every other endpoint gets an unchanged body.
    openai_base_url: str = ""
    openai_max_retries: int = 4  # transient (429/5xx/network) backoff attempts

    # --- Provider request compatibility (phase-57) ---
    # Endpoints disagree about the request schema (a reasoning-family model rejects `max_tokens` and
    # wants `max_completion_tokens`; Mistral rejects `stream_options`). The transport reads the
    # provider's own 400, repairs the body, retries, and remembers the working shape here — keyed by
    # "<endpoint>|<model>", since the same base URL answers differently per model. Learned
    # automatically; safe to clear (it is simply re-learned).
    llm_param_quirks: str = ""
    openai_max_param_repairs: int = 4  # per-call cap on request repairs (separate from retries)
    openai_param_learning: bool = True  # off → seed table only; nothing learned or written

    # --- Reasoning models & truncated turns (phase-64) ---
    # A turn the provider cuts off at the output cap (a reasoning model thinking past max_tokens)
    # or returns empty is NOT the model finishing. The loop appends a corrective turn and continues,
    # at most this many times per loop, then stops with `max_tokens` so the caller records the work
    # as unfinished rather than done.
    llm_max_truncated_turns: int = 2
    # How much the model should think, sent as the OpenAI-compatible `reasoning` object
    # (OpenRouter's unified shape; endpoints that reject it get it dropped by the phase-57 ladder).
    # Blank = provider default (a byte-identical request). `none` | `low` | `medium` | `high`.
    llm_reasoning_effort: str = ""
    # A hard token budget for the model's thinking; 0 = unset. Combines with the effort level.
    llm_reasoning_max_tokens: int = 0

    # --- Agent tools (phase-21) ---
    # Prebuilt generated-app skeleton copied into /workspace by instantiate_skeleton (phase-22/23).
    # Relative paths resolve from the repo root; absolute paths are used as-is.
    app_skeleton_dir: str = "templates/app-skeleton"
    # Cap on command output fed back to the model (the tail is kept). 8000 → 16000 in phase-64: a
    # truncated typecheck tail hid the very errors a phase was asked to fix.
    tool_output_max_chars: int = 16000

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

    # --- Observability (phase-46) ---
    # Warn once spend crosses this fraction of a cap. Approaching a cap silently and then halting
    # mid-run is the surprise this removes; 0 disables the warning.
    budget_warn_ratio: float = 0.8

    # --- Live-failure repair loop (phase-40) ---
    # The **outer** bound (D7 one level up): how many validate→repair→redeploy→re-validate cycles
    # before escalating. The inner repair loop has its own cap; a broken deploy config must not be
    # able to drive infinite redeploys.
    validate_max_cycles: int = 2

    # --- MongoDB ---
    mongodb_uri: str = "mongodb://localhost:27017"
    forgeflow_meta_db: str = "forgeflow_meta"
    # Shared cluster that hosts generated apps' per-project databases in platform mode (phase-36,
    # D8). Blank → reuse `mongodb_uri` (fine for local dev). A per-project DB name is appended, so
    # this is a cluster base URI without a database path. Secret-ish (may carry Atlas creds).
    app_db_cluster_uri: str = ""
    # The same cluster, addressed the way a **sandbox** must address it. A sandbox is on an
    # `internal` docker network: a `localhost` URI resolves to the sandbox itself and there is no
    # route to the host, so the generated app's queries just time out. Blank → inject
    # `app_db_cluster_uri` unchanged (correct when the control plane runs in docker too).
    app_db_sandbox_uri: str = ""

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

    # Dependency installs need the npm registry, which the sandbox's sealed network forbids: when
    # true, a package-manager fetch command borrows the routed egress network for its duration and
    # hands it straight back (app.sandbox.network). Off → the sandbox never reaches the registry
    # and only pre-installed dependencies work.
    sandbox_install_network: bool = True
    # Installs are slow (a cold pnpm install of the skeleton pulls hundreds of packages), so they
    # get their own, longer wall clock than an ordinary command.
    sandbox_install_timeout_s: int = 1800

    # --- Exec & terminal (phase-13) ---
    sandbox_exec_timeout_s: int = 600  # default per-command wall clock before the process is killed
    sandbox_max_concurrent_execs: int = 3  # per project
    sandbox_shell: str = "/bin/bash"  # interactive terminal shell inside the sandbox

    # --- Live preview (phase-15) ---
    # Commands run inside the sandbox against the phase-22 skeleton layout ({port} is substituted).
    preview_fe_dir: str = "frontend"
    preview_be_dir: str = "backend"
    # --strictPort is load-bearing (phase-59): the proxy routes to a FIXED port, so a Vite that
    # slides to the next free one is unreachable while reporting itself healthy. Fail instead.
    preview_fe_cmd: str = "pnpm dev --host 0.0.0.0 --port {port} --strictPort"
    preview_be_cmd: str = "pnpm dev"  # port arrives via the injected PORT env var
    # Warn once the sandbox process count reaches this fraction of sandbox_pids_limit; 0 disables.
    preview_pid_warn_ratio: float = 0.8
    preview_fe_port: int = 5173
    preview_be_port: int = 3001
    preview_health_timeout_s: int = 90  # how long start() waits for both servers to answer
    preview_health_path: str = "/"  # FE probe path; the BE probe uses /health (skeleton contract)
    preview_be_health_path: str = "/health"
    # Dedicated *internal* docker network for preview: sandbox↔proxy only, never the host (§7).
    preview_network_name: str = "forgeflow-preview"
    # Routed network the sandbox joins *only* for a live validation run (phase-39), then leaves.
    egress_network_name: str = "forgeflow-egress"
    # A preview needs two containers that are not the sandbox: the proxy (the only route in) and
    # the generated apps' database. Restarting docker leaves both exited while the sandbox itself
    # comes back on demand, so previews break in two ways that both point away from the cause.
    # When true, preview start restarts whatever it finds stopped on the preview network.
    preview_autostart_deps: bool = True
    # How long preview start waits for a dependency it restarted to serve (mongo needs a moment,
    # and the generated backend only dials it once, at boot).
    preview_deps_ready_timeout_s: int = 45
    preview_base_domain: str = (
        ""  # set in prod → https://<project>.<domain>; else *.preview.localhost
    )

    # --- Design providers (phase 17/18) ---
    # Stitch (Google Labs) speaks MCP at https://stitch.googleapis.com/mcp. Credentials, in the
    # order the transport tries them: an API key (Stitch → Settings → API keys), a Google access
    # token (gcloud), or a client-credentials exchange against a proxy token endpoint. Blank creds
    # → provider health()=down and the design stage falls back (figma/fake) rather than crashing.
    stitch_api_key: str = ""  # sent as X-Goog-Api-Key — the simplest path
    stitch_access_token: str = ""  # sent as Authorization: Bearer (externally refreshed, ~1h)
    stitch_gcp_project: str = ""  # sent as X-Goog-User-Project (quota/billing attribution)
    stitch_client_id: str = ""
    stitch_client_secret: str = ""
    stitch_quota_monthly: int = 350
    stitch_mcp_url: str = "https://stitch.googleapis.com/mcp"
    stitch_token_url: str = ""  # OAuth2 token endpoint (client-credentials grant)
    stitch_scope: str = ""  # optional OAuth scope
    # Stitch is project-scoped. Blank → a project is created on first use and reused; set this to
    # pin generations to an existing project (visible in the Stitch web app).
    stitch_project_id: str = ""
    # Optional generation model, e.g. GEMINI_3_FLASH (standard quota) or GEMINI_3_PRO
    # (experimental, much smaller quota). Blank → the API's own default.
    stitch_model_id: str = ""
    # Generation is asynchronous: the tool answers once the screen exists, which can be before its
    # code does. How long to keep re-reading the screen for its HTML before giving up (and falling
    # back) rather than saving a blank design.
    stitch_ready_timeout_s: float = 90.0
    stitch_timeout_s: float = 60.0  # per-call wall clock for handshake + metadata reads
    # Generation is far slower than a read: a multi-screen prompt measured ~76s against the live
    # endpoint, so the 60s read timeout cut the connection *after* Stitch had already generated
    # (and charged for) the screens. Generation/edit calls get their own, much longer budget.
    stitch_generate_timeout_s: float = 300.0
    stitch_max_retries: int = 2  # extra attempts on transient errors (total = 1 + this)
    # Given a broad brief, Stitch's model may answer with a proposal and a question ("shall I
    # proceed with these 5 screens?") instead of a design. The tool takes no session handle, so
    # the transport answers by re-asking with the confirmation folded into the prompt. A proposal
    # only needs one yes; a question that survives that wants a decision no blind retry can make,
    # so the stage parks it for the user instead (app/design/questions.py). 0 disables it.
    stitch_clarify_max_replies: int = 1
    stitch_token_skew_s: float = 60.0  # refresh this many seconds before real expiry
    stitch_quota_soft_ratio: float = 0.9  # used/limit >= this → health() degraded
    # Figma MCP (phase-18): a static access token (no OAuth refresh). Blank token → health()=down.
    figma_token: str = ""
    figma_mcp_url: str = ""  # Figma MCP server base URL
    figma_timeout_s: float = 60.0  # per-call wall clock
    figma_max_retries: int = 2  # extra attempts on transient errors
    design_provider: str = "stitch"
    # Automatic fallback order when the active provider fails recoverably — quota/auth/transient
    # (phase-48, D10). Tried after the active provider, deduped, capability-filtered. "fake" last
    # so a design can always be produced rather than dead-ending. Empty disables auto-fallback.
    design_fallback_chain: str = "figma,fake"
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
    # Where the backend deploys (phase-58). "vercel" uploads the workspace inline and needs no git
    # host; "render" clones deploy_repo_url below.
    deploy_be_provider: str = "vercel"
    deploy_repo_url: str = ""  # git repo Render builds from; unused when deploy_be_provider=vercel
    deploy_region: str = ""  # optional provider region for the backend service
    deploy_health_timeout_s: int = 180  # seconds to poll a deploy until it goes live

    # --- Repair loop (phase 29-31) ---
    repair_max_iterations: int = 5
    repair_stall_threshold: int = 2
    # Regression guard: escalate once patches have broken previously-passing tests this many times
    # (phase-55 promoted this from a hard-coded constant so the eval harness can sweep it).
    repair_max_regressions: int = 2
    # Minimal-context budget (phase-29). The invariant is "never feed the whole repo when a diff
    # suffices" (§9), so the assembled context is hard-capped: oversized parts are trimmed by
    # priority (failure frequency) and what was dropped is recorded on the context.
    repair_context_max_chars: int = 24_000  # whole-context cap
    repair_context_max_file_chars: int = 8_000  # per-file cap before truncation
    repair_context_diff_max_chars: int = 8_000  # cap on the since-last-passing diff
    # Reachability (phase-63). A failing spec's *imports* name the code under test far better than
    # the directory it happens to sit in; the walk is bounded before the char budget applies.
    repair_import_max_depth: int = 3  # how far the import walk follows a failing spec (0 = off)
    repair_import_max_files: int = 24  # hard cap on sources one walk may discover
    # The bounded escape hatch: when the fix genuinely lives in a file the analyzer did not reach,
    # the agent may name it. Still never a test file, and every grant is recorded on the context.
    repair_request_file_max: int = 3  # files the agent may request per attempt (0 = off)
    # An attempt that writes nothing re-poses a byte-identical question next iteration, so the loop
    # allows one retry (sampling variance) and then escalates with the agent's own explanation.
    repair_max_noop_attempts: int = 2

    # --- Build verification & self-heal (phase-55) ---
    # After codegen, the build is verified (install → typecheck → boot → placeholder check) and
    # code-class failures are self-healed through the existing bounded repair loop. Every bound here
    # keeps that loop finite (D7 / Golden Rule 5).
    build_integration_max_iterations: int = (
        3  # repair cap for a build (separate from the test loop)
    )
    build_max_wall_clock_s: int = 3600  # overall build deadline — the outermost bound
    build_typecheck_cmd: str = "pnpm typecheck"  # the typecheck gate command
    build_typecheck_timeout_s: int = 600  # wall clock for the typecheck gate
    build_typecheck_max_failures: int = 20  # cap on synthetic results emitted per typecheck run
    # Bounded ring of a preview process's most recent output lines, kept as boot-failure evidence.
    preview_log_tail_lines: int = 200

    # --- Phased build plan (phase-56) ---
    # The build is planned into small phases and implemented one at a time, each with its own small
    # context, its own typecheck gate and its own commit. Every bound here caps cost as well as
    # runtime: N phases × (implement + fixes) is the shape of a build's spend.
    build_max_phases: int = 8  # caps plan size and therefore cost
    build_phase_max_tool_turns: int = 60  # per-phase tool-loop bound
    build_phase_max_fix_attempts: int = 2  # per-phase typecheck fix sub-loop
    build_plan_dir: str = "phase-plan"  # workspace folder holding the plan (a FE/BE sibling)
    build_plan_in_workspace: bool = True  # also write the plan into the workspace, not just Mongo
    # phase-64: a phase is `done` only with evidence — at least one file written and a closing
    # summary — never merely "the loop ended and the (unchanged) workspace still typechecks".
    build_phase_noop_retries: int = 1  # extra attempts, with explicit feedback, for a no-op phase
    build_phase_require_summary: bool = True  # off → a missing closing line is synthesised


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
        """Register a higher-priority override source (idempotent). Used by phase-51 (DB layer)."""
        if provider not in self._providers:
            self._providers.insert(0, provider)

    def has_provider(self, provider: SettingProvider) -> bool:
        return provider in self._providers

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


# --------------------------------------------------------------------- subprocess environment

#: The only variables a child process inherits. Everything else — ``ANTHROPIC_API_KEY``,
#: ``FERNET_KEY``, ``SECRET_KEY``, provider tokens, ``MONGODB_URI`` — is withheld by construction.
#:
#: A subprocess started by the control plane may end up running generated code or tooling that
#: handles it, so inheriting the full environment would hand a `printenv` every platform secret
#: (phase-47, threat model §5.3). An allowlist fails closed: a newly added secret is withheld
#: automatically, whereas a denylist would have to be remembered.
SUBPROCESS_ENV_ALLOWLIST: tuple[str, ...] = (
    "PATH",  # required to resolve git/node/shell
    "HOME",
    "LANG",
    "LC_ALL",
    "TZ",
    # Windows needs these for CreateProcess and temp-file resolution to work at all.
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "COMSPEC",
    "PATHEXT",
    "TEMP",
    "TMP",
    "USERPROFILE",
)


def subprocess_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """A minimal environment for a child process: allowlisted vars plus explicit ``extra``.

    Lives here because this module is the single place permitted to read ``os.environ``
    (see the module docstring); callers get a curated mapping instead.
    """
    env = {name: os.environ[name] for name in SUBPROCESS_ENV_ALLOWLIST if name in os.environ}
    if extra:
        env.update(extra)
    return env


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
