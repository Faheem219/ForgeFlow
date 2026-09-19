"""The catalog of admin-editable platform settings (phase-51).

**Every** field on :class:`~app.core.config.Settings` is listed here — the directive is that the
whole environment is configurable from the admin panel, not a curated subset — and a test
(``tests/config/test_registry_complete.py``) fails the build if a new field is added without a
registry entry. Each entry carries the metadata the dashboard needs (category, type, description,
bounds/choices, sensitivity, restart semantics) plus the validation applied on write.

Three rules keep it safe:

- **Every value is coerced + validated** to the field's type and range before it is stored, so the
  DB layer can never feed the resolver a value the code would choke on.
- **Sensitive keys are write-only and encrypted at rest.** API keys and tokens may be *set* from
  the panel, but they are Fernet-encrypted into the ``PlatformSetting`` row (``config_admin``),
  decrypted only at read time (``config_db``), and never returned to a client — reads yield a mask.
- **Locked keys cannot be written at all.** ``fernet_key`` protects the encrypted values themselves
  (storing it beside them would defeat the encryption) and the Mongo connection keys are read before
  the DB layer exists, so an override there would silently do nothing. They are still *shown*, with
  their source and the reason they are read-only.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic_core import PydanticUndefined

from app.core.config import Settings
from app.core.errors import UserError
from app.db.models.enums import SettingCategory as Cat

ValueType = str  # one of: "str" | "int" | "float" | "bool" | "enum" | "json"

#: Why a locked key is read-only, shown verbatim in the dashboard.
BOOTSTRAP_SECRET = (
    "Encrypts every other secret stored here — keeping it in the same database would defeat that. "
    "Set it in the environment."
)
BOOTSTRAP_DB = (
    "Read while connecting to Mongo, before the admin config layer exists — an override here could "
    "never take effect. Set it in the environment."
)


@dataclass(frozen=True)
class ConfigKey:
    """Metadata + validation for one admin-editable setting."""

    key: str
    category: Cat
    type: ValueType
    description: str
    #: Secrets: writable but write-only — encrypted at rest, never read back to a client.
    sensitive: bool = False
    #: Takes effect only after a restart (or, for sandbox limits, for newly-created containers).
    restart_required: bool = False
    #: Read-only: an override is impossible or unsafe. ``locked_reason`` explains why.
    locked: bool = False
    locked_reason: str = ""
    #: Allowed values for ``type == "enum"``.
    choices: tuple[str, ...] = ()
    #: Inclusive numeric bounds for int/float.
    minimum: float | None = None
    maximum: float | None = None
    #: Whether a value may be ``None`` (e.g. an "unset" budget cap).
    nullable: bool = False
    #: Extra, key-specific validation (raises ``UserError``). Runs after type coercion.
    extra: Callable[[Any], None] | None = field(default=None, compare=False)

    @property
    def env_var(self) -> str:
        """The environment variable that supplies this key when no admin override exists."""
        return self.key.upper()

    def default(self) -> Any:
        """The **code** default — what the key falls back to when neither DB nor env supplies it."""
        info = Settings.model_fields.get(self.key)
        if info is None or info.default is PydanticUndefined:
            return None
        return info.default


# --------------------------------------------------------------------------- validators


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


def _url_or_blank(value: Any) -> None:
    text = str(value).strip()
    if text and not text.startswith(("http://", "https://")):
        raise UserError("must be blank or an http(s) URL")


def _mongo_uri_or_blank(value: Any) -> None:
    text = str(value).strip()
    if text and not text.startswith(("mongodb://", "mongodb+srv://")):
        raise UserError("must be blank or a mongodb:// URI")


_MEM = re.compile(r"^\d+(\.\d+)?\s*[bkmgBKMG]?$")


def _memory_size(value: Any) -> None:
    if not _MEM.match(str(value).strip()):
        raise UserError("must be a docker memory size such as '512m' or '1g'")


def _design_chain(value: Any) -> None:
    """A comma-separated fallback order over known providers; blank disables auto-fallback."""
    text = str(value).strip()
    if not text:
        return
    unknown = [p for p in (s.strip() for s in text.split(",")) if p and p not in DESIGN_PROVIDERS]
    if unknown:
        raise UserError(f"has unknown providers: {', '.join(unknown)}")


def _email_or_blank(value: Any) -> None:
    text = str(value).strip()
    if text and ("@" not in text or text.startswith("@") or text.endswith("@")):
        raise UserError("must be blank or an email address")


def _abs_path(value: Any) -> None:
    text = str(value).strip()
    if not text.startswith("/"):
        raise UserError("must be an absolute path")


def _leading_slash(value: Any) -> None:
    if not str(value).strip().startswith("/"):
        raise UserError("must start with '/'")


def _docker_name(value: Any) -> None:
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$", str(value).strip()):
        raise UserError("must be a valid docker name (alphanumerics, '_', '.', '-')")


DESIGN_PROVIDERS = ("stitch", "figma", "fake")
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

# --------------------------------------------------------------------------- the registry
#
# Keys are ``Settings`` field names, so the DB layer overrides them under the same name the resolver
# reads. Order within a category is the order the dashboard renders.

_KEYS: tuple[ConfigKey, ...] = (
    # ---------------------------------------------------------------- models (LLM provider)
    ConfigKey(
        "llm_provider",
        Cat.models,
        "enum",
        "Chat backend for every agent: Anthropic Messages API, or any OpenAI-compatible "
        "Chat Completions endpoint (OpenAI, Azure, Groq, OpenRouter, vLLM, Ollama…).",
        choices=("anthropic", "openai"),
    ),
    ConfigKey(
        "model_codegen",
        Cat.models,
        "str",
        "Model id for real code/test/repair work. Use an id valid for the active provider.",
    ),
    ConfigKey(
        "model_routing",
        Cat.models,
        "str",
        "Cheap model for summaries/routing (cost discipline).",
    ),
    ConfigKey(
        "model_classify",
        Cat.models,
        "str",
        "Model for cheap classification calls (e.g. deciding whether a change request needs a "
        "phased build). Blank uses MODEL_ROUTING.",
    ),
    ConfigKey(
        "anthropic_api_key",
        Cat.models,
        "str",
        "Anthropic API key. Stored encrypted; never displayed again once saved.",
        sensitive=True,
    ),
    ConfigKey(
        "anthropic_base_url",
        Cat.models,
        "str",
        "Override the Anthropic API base URL (blank = SDK default).",
        extra=_url_or_blank,
    ),
    ConfigKey(
        "openai_api_key",
        Cat.models,
        "str",
        "API key for the OpenAI-compatible endpoint. Stored encrypted; never displayed again.",
        sensitive=True,
    ),
    ConfigKey(
        "openai_base_url",
        Cat.models,
        "str",
        "Base URL of the OpenAI-compatible endpoint, e.g. http://localhost:11434/v1 (Ollama) or "
        "https://openrouter.ai/api/v1. Blank = the OpenAI default.",
        extra=_url_or_blank,
    ),
    ConfigKey(
        "llm_param_quirks",
        Cat.models,
        "json",
        'Learned per-endpoint/model request repairs: {"<base-url>|<model>": [{"adaptation": …}]}. '
        "Written automatically when a provider rejects a parameter (e.g. a model that needs "
        "max_completion_tokens instead of max_tokens). Safe to clear — it is re-learned on the "
        "next call.",
        extra=_valid_json_object,
    ),
    ConfigKey(
        "openai_max_param_repairs",
        Cat.models,
        "int",
        "Per-call cap on request repairs when an endpoint rejects a parameter. Counted separately "
        "from the transient-retry budget; each repair must also be novel, so the loop is bounded "
        "twice over.",
        minimum=0,
        maximum=10,
    ),
    ConfigKey(
        "openai_param_learning",
        Cat.models,
        "bool",
        "Learn and persist request repairs from provider errors. Off = use only the built-in seed "
        "rules; nothing is written to llm_param_quirks.",
    ),
    ConfigKey(
        "anthropic_max_output_tokens",
        Cat.models,
        "int",
        "Max output tokens per model call. A reasoning model's thinking counts against this; "
        "16384 comfortably holds a thought plus a 300-line file.",
        minimum=1,
        maximum=200_000,
    ),
    ConfigKey(
        "llm_max_truncated_turns",
        Cat.models,
        "int",
        "How many times per tool loop a turn cut off at the output cap (or returned empty) is "
        "answered with a corrective nudge before the loop stops as unfinished.",
        minimum=0,
        maximum=10,
    ),
    ConfigKey(
        "llm_reasoning_effort",
        Cat.models,
        "enum",
        "How much a reasoning model should think, sent as the OpenAI-compatible `reasoning` "
        "hint (OpenRouter's unified shape; dropped automatically where rejected). Blank = the "
        "provider's default.",
        choices=("", "none", "low", "medium", "high"),
    ),
    ConfigKey(
        "llm_reasoning_max_tokens",
        Cat.models,
        "int",
        "Token budget for a reasoning model's thinking (0 = unset). Combines with the effort.",
        minimum=0,
        maximum=100_000,
    ),
    ConfigKey(
        "anthropic_max_tool_turns",
        Cat.models,
        "int",
        "Safety bound on a single agent tool-use loop. One turn per tool call, so a large "
        "full-stack build needs hundreds; the loop stays bounded and budget-checked either way.",
        minimum=1,
        maximum=500,
    ),
    ConfigKey(
        "anthropic_max_retries",
        Cat.models,
        "int",
        "Backoff attempts on transient Anthropic errors (429/5xx/network).",
        minimum=0,
        maximum=10,
    ),
    ConfigKey(
        "openai_max_retries",
        Cat.models,
        "int",
        "Backoff attempts on transient errors from the OpenAI-compatible endpoint.",
        minimum=0,
        maximum=10,
    ),
    # ---------------------------------------------------------------- agents
    ConfigKey(
        "app_skeleton_dir",
        Cat.agents,
        "str",
        "Generated-app skeleton copied into /workspace. Relative paths resolve from the repo root.",
    ),
    ConfigKey(
        "tool_output_max_chars",
        Cat.agents,
        "int",
        "Cap on command output fed back to the model (the tail is kept).",
        minimum=500,
        maximum=200_000,
    ),
    ConfigKey(
        "codegen_context_char_budget",
        Cat.agents,
        "int",
        "Per-artifact cap on design/requirements context handed to the codegen agent.",
        minimum=500,
        maximum=200_000,
    ),
    ConfigKey(
        "build_integration_max_iterations",
        Cat.agents,
        "int",
        "Repair-loop iteration cap when self-healing a build (separate from the Test-stage cap). "
        "Only code-class failures ever reach it; the loop is always bounded.",
        minimum=1,
        maximum=20,
    ),
    ConfigKey(
        "build_max_wall_clock_s",
        Cat.agents,
        "int",
        "Overall deadline for one build (codegen + verify + self-heal) — the outermost bound.",
        minimum=60,
        maximum=86_400,
    ),
    ConfigKey(
        "build_typecheck_cmd",
        Cat.agents,
        "str",
        "Command run as the build's typecheck gate (workspace root).",
    ),
    ConfigKey(
        "build_typecheck_timeout_s",
        Cat.agents,
        "int",
        "Wall clock for the build's typecheck gate.",
        minimum=30,
        maximum=7200,
    ),
    ConfigKey(
        "build_typecheck_max_failures",
        Cat.agents,
        "int",
        "Cap on synthetic repair targets emitted from one typecheck run (one per failing file).",
        minimum=1,
        maximum=200,
    ),
    ConfigKey(
        "build_max_phases",
        Cat.agents,
        "int",
        "Maximum phases in a build plan. Caps plan size and therefore the cost of a build, since "
        "each phase is its own model loop.",
        minimum=1,
        maximum=30,
    ),
    ConfigKey(
        "build_phase_max_tool_turns",
        Cat.agents,
        "int",
        "Tool-loop turn bound for ONE build phase (the per-phase equivalent of "
        "anthropic_max_tool_turns).",
        minimum=5,
        maximum=250,
    ),
    ConfigKey(
        "build_phase_max_fix_attempts",
        Cat.agents,
        "int",
        "How many times a phase may be handed its own typecheck errors before it is recorded as "
        "failed and carried into integration repair.",
        minimum=0,
        maximum=5,
    ),
    ConfigKey(
        "build_phase_noop_retries",
        Cat.agents,
        "int",
        "Extra attempts, with explicit feedback, for a build phase whose loop finished without "
        "writing a single file. After these it is recorded failed ('wrote nothing'), never done.",
        minimum=0,
        maximum=3,
    ),
    ConfigKey(
        "build_phase_require_summary",
        Cat.agents,
        "bool",
        "A phase must end with the model's own closing summary to count as done; a model that "
        "writes its files and then produces nothing through every nudge fails the phase. Off = "
        "the missing line is synthesised from the files written and the phase can be done.",
    ),
    ConfigKey(
        "build_plan_dir",
        Cat.agents,
        "str",
        "Workspace folder the phase plan is written to (a sibling of frontend/ and backend/).",
    ),
    ConfigKey(
        "build_plan_in_workspace",
        Cat.agents,
        "bool",
        "Write the phase plan into the workspace as well as persisting it as an artifact.",
    ),
    # ---------------------------------------------------------------- budget & pricing
    ConfigKey(
        "budget_cap_inr_per_project",
        Cat.budget,
        "float",
        "Per-project spend cap in ₹ (blank = uncapped).",
        nullable=True,
        extra=_positive,
    ),
    ConfigKey(
        "budget_cap_inr_global",
        Cat.budget,
        "float",
        "Platform-wide spend cap in ₹ (blank = uncapped).",
        nullable=True,
        extra=_positive,
    ),
    ConfigKey(
        "budget_warn_ratio",
        Cat.budget,
        "float",
        "Warn once spend crosses this fraction of a cap. 0 disables the warning.",
        minimum=0,
        maximum=1,
    ),
    ConfigKey(
        "model_pricing_json",
        Cat.budget,
        "json",
        'Per-model ₹/1M-token pricing: {"<model>": {"input": .., "output": ..}}. '
        "Blank = built-in defaults. Required to cost a model the platform does not know.",
        extra=_valid_json_object,
    ),
    # ---------------------------------------------------------------- design providers
    ConfigKey(
        "design_provider",
        Cat.providers,
        "enum",
        "Active design provider.",
        choices=DESIGN_PROVIDERS,
    ),
    ConfigKey(
        "design_fallback_chain",
        Cat.providers,
        "str",
        "Comma-separated fallback order when the active provider fails recoverably "
        "(quota/auth/transient). Blank disables auto-fallback.",
        extra=_design_chain,
    ),
    ConfigKey(
        "design_max_images",
        Cat.providers,
        "int",
        "Max screenshots accepted per design intake.",
        minimum=1,
        maximum=50,
    ),
    ConfigKey(
        "design_max_image_bytes",
        Cat.providers,
        "int",
        "Per-screenshot size cap in bytes.",
        minimum=1024,
        maximum=52_428_800,
    ),
    ConfigKey(
        "stitch_api_key",
        Cat.providers,
        "str",
        "Stitch API key (Stitch → Settings → API keys), sent as X-Goog-Api-Key. The simplest way "
        "to authenticate. Stored encrypted; never displayed again.",
        sensitive=True,
    ),
    ConfigKey(
        "stitch_access_token",
        Cat.providers,
        "str",
        "Google access token for Stitch (e.g. from gcloud), sent as a bearer token. Expires in "
        "about an hour and is not refreshed here. Stored encrypted; never displayed again.",
        sensitive=True,
    ),
    ConfigKey(
        "stitch_gcp_project",
        Cat.providers,
        "str",
        "Cloud project billed/quota-attributed for Stitch calls (X-Goog-User-Project).",
    ),
    ConfigKey(
        "stitch_project_id",
        Cat.providers,
        "str",
        "Existing Stitch project to generate into. Blank creates one on first use.",
    ),
    ConfigKey(
        "stitch_model_id",
        Cat.providers,
        "str",
        "Stitch generation model, e.g. GEMINI_3_FLASH (standard quota) or GEMINI_3_PRO "
        "(experimental, far smaller quota). Blank uses the API default.",
    ),
    ConfigKey(
        "stitch_ready_timeout_s",
        Cat.providers,
        "float",
        "How long to wait for an asynchronously generated Stitch screen to carry its HTML before "
        "falling back, rather than saving a blank design.",
        minimum=5,
        maximum=600,
    ),
    ConfigKey(
        "stitch_client_id",
        Cat.providers,
        "str",
        "OAuth client id, only for a proxy token endpoint that implements client-credentials.",
    ),
    ConfigKey(
        "stitch_client_secret",
        Cat.providers,
        "str",
        "OAuth client secret for that proxy endpoint. Stored encrypted; never displayed again.",
        sensitive=True,
    ),
    ConfigKey(
        "stitch_mcp_url",
        Cat.providers,
        "str",
        "Stitch MCP endpoint (JSON-RPC over HTTP). Default: https://stitch.googleapis.com/mcp",
        extra=_url_or_blank,
    ),
    ConfigKey(
        "stitch_token_url",
        Cat.providers,
        "str",
        "OAuth2 token endpoint for the client-credentials path. Google's own token endpoint does "
        "not implement that grant — leave blank unless you run a proxy that does.",
        extra=_url_or_blank,
    ),
    ConfigKey(
        "stitch_scope", Cat.providers, "str", "Optional OAuth scope for that token endpoint."
    ),
    ConfigKey(
        "stitch_quota_monthly",
        Cat.providers,
        "int",
        "Stitch generations allowed per monthly window (free tier ≈ 350).",
        minimum=1,
        maximum=1_000_000,
    ),
    ConfigKey(
        "stitch_quota_soft_ratio",
        Cat.providers,
        "float",
        "used/limit at or above this ratio reports the provider as degraded.",
        minimum=0,
        maximum=1,
    ),
    ConfigKey(
        "stitch_timeout_s",
        Cat.providers,
        "float",
        "Per-call wall clock for the Stitch handshake and metadata reads (seconds).",
        minimum=1,
        maximum=600,
    ),
    ConfigKey(
        "stitch_generate_timeout_s",
        Cat.providers,
        "float",
        "Per-call wall clock for Stitch generate/edit (seconds). Generation is far slower than a "
        "read — a multi-screen prompt takes 1-3 minutes — and a timeout here throws away a "
        "generation Stitch has already charged for.",
        minimum=1,
        maximum=900,
    ),
    ConfigKey(
        "stitch_max_retries",
        Cat.providers,
        "int",
        "Extra attempts on transient Stitch errors.",
        minimum=0,
        maximum=10,
    ),
    ConfigKey(
        "stitch_clarify_max_replies",
        Cat.providers,
        "int",
        "How many times to confirm a Stitch proposal when it answers with a question instead of "
        'a design ("shall I proceed with these 5 screens?"). Each confirmation is another '
        "generate call; a question that survives them is put to the user in the design stage.",
        minimum=0,
        maximum=5,
    ),
    ConfigKey(
        "stitch_token_skew_s",
        Cat.providers,
        "float",
        "Refresh the Stitch token this many seconds before real expiry.",
        minimum=0,
        maximum=3600,
    ),
    ConfigKey(
        "figma_token",
        Cat.providers,
        "str",
        "Figma personal access token. Stored encrypted; never displayed again.",
        sensitive=True,
    ),
    ConfigKey(
        "figma_mcp_url", Cat.providers, "str", "Figma MCP server base URL.", extra=_url_or_blank
    ),
    ConfigKey(
        "figma_timeout_s",
        Cat.providers,
        "float",
        "Per-call wall clock for Figma (seconds).",
        minimum=1,
        maximum=600,
    ),
    ConfigKey(
        "figma_max_retries",
        Cat.providers,
        "int",
        "Extra attempts on transient Figma errors.",
        minimum=0,
        maximum=10,
    ),
    # ---------------------------------------------------------------- sandbox
    ConfigKey(
        "sandbox_image",
        Cat.sandbox,
        "str",
        "Docker image backing every project sandbox.",
        restart_required=True,
    ),
    ConfigKey(
        "sandbox_cpu_limit",
        Cat.sandbox,
        "float",
        "CPU cores per sandbox.",
        restart_required=True,
        minimum=0.1,
        maximum=16,
        extra=_positive,
    ),
    ConfigKey(
        "sandbox_mem_limit",
        Cat.sandbox,
        "str",
        "Memory per sandbox (e.g. '1g', '512m').",
        restart_required=True,
        extra=_memory_size,
    ),
    ConfigKey(
        "sandbox_pids_limit",
        Cat.sandbox,
        "int",
        "Max processes per sandbox (fork-bomb guard).",
        restart_required=True,
        minimum=16,
        maximum=4096,
    ),
    ConfigKey(
        "sandbox_read_only_root",
        Cat.sandbox,
        "bool",
        "Mount the container root read-only (/workspace, /home/app and /tmp stay writable).",
        restart_required=True,
    ),
    ConfigKey(
        "sandbox_install_network",
        Cat.sandbox,
        "bool",
        "Let dependency-install commands borrow the routed egress network for their duration "
        "(off → the sandbox can never reach the npm registry).",
    ),
    ConfigKey(
        "sandbox_install_timeout_s",
        Cat.sandbox,
        "int",
        "Wall clock for a dependency install (longer than an ordinary command).",
        minimum=30,
        maximum=14_400,
    ),
    ConfigKey(
        "sandbox_idle_timeout_s",
        Cat.sandbox,
        "int",
        "Idle seconds before a sandbox is reaped.",
        minimum=30,
        maximum=86_400,
    ),
    ConfigKey(
        "sandbox_reap_interval_s",
        Cat.sandbox,
        "int",
        "How often the idle reaper sweeps (seconds).",
        minimum=5,
        maximum=3600,
    ),
    ConfigKey(
        "sandbox_exec_timeout_s",
        Cat.sandbox,
        "int",
        "Default per-command wall clock before the process is killed.",
        minimum=5,
        maximum=7200,
    ),
    ConfigKey(
        "sandbox_max_concurrent_execs",
        Cat.sandbox,
        "int",
        "Concurrent commands allowed per project.",
        minimum=1,
        maximum=32,
    ),
    ConfigKey(
        "sandbox_shell",
        Cat.sandbox,
        "str",
        "Interactive terminal shell inside the sandbox.",
        extra=_abs_path,
    ),
    ConfigKey(
        "workspace_max_file_bytes",
        Cat.sandbox,
        "int",
        "Per-file read/write cap for the workspace filesystem API.",
        minimum=1024,
        maximum=104_857_600,
    ),
    # ---------------------------------------------------------------- live preview
    ConfigKey("preview_fe_dir", Cat.preview, "str", "Frontend directory inside the workspace."),
    ConfigKey("preview_be_dir", Cat.preview, "str", "Backend directory inside the workspace."),
    ConfigKey(
        "preview_fe_cmd",
        Cat.preview,
        "str",
        "Frontend dev-server command ({port} is substituted).",
    ),
    ConfigKey(
        "preview_be_cmd",
        Cat.preview,
        "str",
        "Backend dev-server command (the port arrives via the injected PORT env var).",
    ),
    ConfigKey(
        "preview_pid_warn_ratio",
        Cat.preview,
        "float",
        "Warn when the sandbox process count reaches this fraction of SANDBOX_PIDS_LIMIT; "
        "0 disables.",
        minimum=0.0,
        maximum=1.0,
    ),
    ConfigKey(
        "preview_fe_port",
        Cat.preview,
        "int",
        "Frontend port inside the sandbox.",
        minimum=1,
        maximum=65_535,
    ),
    ConfigKey(
        "preview_be_port",
        Cat.preview,
        "int",
        "Backend port inside the sandbox.",
        minimum=1,
        maximum=65_535,
    ),
    ConfigKey(
        "preview_health_timeout_s",
        Cat.preview,
        "int",
        "How long preview start-up waits for both servers to answer.",
        minimum=5,
        maximum=1800,
    ),
    ConfigKey(
        "preview_health_path",
        Cat.preview,
        "str",
        "Frontend health-probe path.",
        extra=_leading_slash,
    ),
    ConfigKey(
        "preview_be_health_path",
        Cat.preview,
        "str",
        "Backend health-probe path (skeleton contract).",
        extra=_leading_slash,
    ),
    ConfigKey(
        "preview_network_name",
        Cat.preview,
        "str",
        "Internal docker network for preview traffic (sandbox↔proxy only, never the host).",
        restart_required=True,
        extra=_docker_name,
    ),
    ConfigKey(
        "egress_network_name",
        Cat.preview,
        "str",
        "Routed docker network the sandbox joins only during a live validation run.",
        restart_required=True,
        extra=_docker_name,
    ),
    ConfigKey(
        "preview_autostart_deps",
        Cat.preview,
        "bool",
        "Restart the stopped containers a preview depends on (the proxy and the generated-app "
        "database) when a preview starts, instead of only warning about them.",
    ),
    ConfigKey(
        "preview_deps_ready_timeout_s",
        Cat.preview,
        "int",
        "How long preview start waits for a restarted dependency to become healthy.",
        minimum=0,
        maximum=600,
    ),
    ConfigKey(
        "preview_base_domain",
        Cat.preview,
        "str",
        "Wildcard domain for preview URLs in production. Blank = *.preview.localhost.",
    ),
    ConfigKey(
        "preview_log_tail_lines",
        Cat.preview,
        "int",
        "How many recent output lines each preview process keeps as boot-failure evidence.",
        minimum=10,
        maximum=5000,
    ),
    # ---------------------------------------------------------------- testing & live validation
    ConfigKey(
        "test_runner_timeout_s",
        Cat.testing,
        "int",
        "Wall clock for a single suite invocation in the sandbox.",
        minimum=30,
        maximum=7200,
    ),
    ConfigKey(
        "live_test_selection",
        Cat.testing,
        "enum",
        "Which E2E subset runs against production. An untagged suite always runs in full.",
        choices=("smoke", "critical", "both", "all"),
    ),
    ConfigKey(
        "live_test_timeout_s",
        Cat.testing,
        "int",
        "Wall clock for the live Playwright invocation.",
        minimum=30,
        maximum=7200,
    ),
    ConfigKey(
        "live_warmup_attempts",
        Cat.testing,
        "int",
        "Probes of a sleeping free-tier deployment before giving up (so results reflect real "
        "failures, not spin-up).",
        minimum=0,
        maximum=60,
    ),
    ConfigKey(
        "live_warmup_interval_s",
        Cat.testing,
        "float",
        "Seconds between warm-up probes.",
        minimum=0.5,
        maximum=120,
    ),
    ConfigKey(
        "live_test_retries",
        Cat.testing,
        "int",
        "Playwright retries for a flaky first hit over the network.",
        minimum=0,
        maximum=5,
    ),
    # ---------------------------------------------------------------- repair loop
    ConfigKey(
        "repair_max_iterations",
        Cat.repair,
        "int",
        "Hard cap on repair-loop iterations (the loop is always bounded).",
        minimum=1,
        maximum=20,
    ),
    ConfigKey(
        "repair_stall_threshold",
        Cat.repair,
        "int",
        "No-progress iterations before the loop escalates to a human.",
        minimum=1,
        maximum=10,
    ),
    ConfigKey(
        "repair_max_regressions",
        Cat.repair,
        "int",
        "Escalate once patches have broken previously-passing tests this many times.",
        minimum=1,
        maximum=10,
    ),
    ConfigKey(
        "repair_context_max_chars",
        Cat.repair,
        "int",
        "Whole-context cap for a repair attempt (never feed the repo when a diff suffices).",
        minimum=1000,
        maximum=400_000,
    ),
    ConfigKey(
        "repair_context_max_file_chars",
        Cat.repair,
        "int",
        "Per-file cap before a file is truncated in the repair context.",
        minimum=500,
        maximum=200_000,
    ),
    ConfigKey(
        "repair_context_diff_max_chars",
        Cat.repair,
        "int",
        "Cap on the since-last-passing diff included in the repair context.",
        minimum=500,
        maximum=200_000,
    ),
    ConfigKey(
        "repair_import_max_depth",
        Cat.repair,
        "int",
        "How far the repair context follows a failing test's imports (0 disables the walk).",
        minimum=0,
        maximum=10,
    ),
    ConfigKey(
        "repair_import_max_files",
        Cat.repair,
        "int",
        "Hard cap on sources one import walk may discover.",
        minimum=0,
        maximum=200,
    ),
    ConfigKey(
        "repair_request_file_max",
        Cat.repair,
        "int",
        "Files the repair agent may request per attempt when the fix lies outside its context.",
        minimum=0,
        maximum=10,
    ),
    ConfigKey(
        "repair_max_noop_attempts",
        Cat.repair,
        "int",
        "Consecutive attempts writing no files before the loop escalates as 'no_patch'.",
        minimum=1,
        maximum=10,
    ),
    ConfigKey(
        "validate_max_cycles",
        Cat.repair,
        "int",
        "Outer bound: validate→repair→redeploy→re-validate cycles before escalating.",
        minimum=1,
        maximum=10,
    ),
    # ---------------------------------------------------------------- deploy
    ConfigKey(
        "deploy_default_mode",
        Cat.deploy,
        "enum",
        "Default credential mode: platform-owned (seamless) or bring-your-own tokens.",
        choices=("seamless", "byo"),
    ),
    ConfigKey(
        "vercel_token",
        Cat.deploy,
        "str",
        "Platform Vercel token for the frontend target. Stored encrypted; never displayed again.",
        sensitive=True,
    ),
    ConfigKey(
        "render_api_key",
        Cat.deploy,
        "str",
        "Platform Render key for the backend target. Stored encrypted; never displayed again.",
        sensitive=True,
    ),
    ConfigKey("vercel_api_url", Cat.deploy, "str", "Vercel API base URL.", extra=_url_or_blank),
    ConfigKey("render_api_url", Cat.deploy, "str", "Render API base URL.", extra=_url_or_blank),
    ConfigKey(
        "deploy_timeout_s",
        Cat.deploy,
        "float",
        "Per-call wall clock for a deploy provider (seconds).",
        minimum=1,
        maximum=600,
    ),
    ConfigKey(
        "deploy_max_retries",
        Cat.deploy,
        "int",
        "Extra attempts on transient deploy-provider errors.",
        minimum=0,
        maximum=10,
    ),
    ConfigKey(
        "deploy_be_provider",
        Cat.deploy,
        "str",
        "Where the backend deploys: 'vercel' (inline upload, no git host) or 'render' (clones "
        "DEPLOY_REPO_URL).",
        choices=("vercel", "render"),
    ),
    ConfigKey(
        "deploy_repo_url",
        Cat.deploy,
        "str",
        "Git repo Render builds the backend from. Unused when DEPLOY_BE_PROVIDER=vercel.",
    ),
    ConfigKey(
        "deploy_region", Cat.deploy, "str", "Optional provider region for the backend service."
    ),
    ConfigKey(
        "deploy_health_timeout_s",
        Cat.deploy,
        "int",
        "Seconds to poll a deployment until it reports live.",
        minimum=10,
        maximum=3600,
    ),
    ConfigKey(
        "infra_scan_max_files",
        Cat.deploy,
        "int",
        "How many workspace files the infra analyzer reads looking for env references.",
        minimum=10,
        maximum=5000,
    ),
    ConfigKey(
        "infra_scan_max_depth",
        Cat.deploy,
        "int",
        "How deep the infra analyzer walks the workspace tree.",
        minimum=1,
        maximum=20,
    ),
    # ---------------------------------------------------------------- storage
    ConfigKey(
        "blob_backend",
        Cat.storage,
        "enum",
        "Where oversized artifact payloads live.",
        choices=("gridfs", "filesystem"),
    ),
    ConfigKey(
        "blob_fs_dir",
        Cat.storage,
        "str",
        "Root directory for the filesystem blob backend (required when it is selected).",
    ),
    ConfigKey(
        "artifact_inline_max_bytes",
        Cat.storage,
        "int",
        "Artifact payloads at or below this size stay inline; larger ones go to the blob store.",
        minimum=256,
        maximum=16_777_216,
    ),
    # ---------------------------------------------------------------- database
    ConfigKey(
        "mongodb_uri",
        Cat.database,
        "str",
        "Control-plane MongoDB connection URI.",
        sensitive=True,
        locked=True,
        locked_reason=BOOTSTRAP_DB,
        extra=_mongo_uri_or_blank,
    ),
    ConfigKey(
        "forgeflow_meta_db",
        Cat.database,
        "str",
        "Control-plane database name.",
        locked=True,
        locked_reason=BOOTSTRAP_DB,
    ),
    ConfigKey(
        "app_db_cluster_uri",
        Cat.database,
        "str",
        "Cluster hosting generated apps' per-project databases in platform mode. Blank reuses the "
        "control-plane URI. Stored encrypted; never displayed again.",
        sensitive=True,
        extra=_mongo_uri_or_blank,
    ),
    ConfigKey(
        "app_db_sandbox_uri",
        Cat.database,
        "str",
        "The same generated-app cluster, addressed the way a sandbox must (it sits on an internal "
        "network with no route to the host, so a localhost URI can never work there). Blank uses "
        "the cluster URI unchanged. Stored encrypted; never displayed again.",
        sensitive=True,
        extra=_mongo_uri_or_blank,
    ),
    # ---------------------------------------------------------------- realtime
    ConfigKey(
        "realtime_ring_size",
        Cat.realtime,
        "int",
        "Per-project replay ring-buffer size for WebSocket events.",
        minimum=16,
        maximum=10_000,
    ),
    ConfigKey(
        "realtime_heartbeat_s",
        Cat.realtime,
        "float",
        "WebSocket heartbeat/ping interval (seconds).",
        minimum=1,
        maximum=300,
    ),
    # ---------------------------------------------------------------- auth & limits
    ConfigKey(
        "jwt_algorithm",
        Cat.auth,
        "enum",
        "Signing algorithm for access tokens.",
        choices=("HS256", "HS384", "HS512"),
    ),
    ConfigKey(
        "access_token_expire_minutes",
        Cat.auth,
        "int",
        "Access-token lifetime in minutes.",
        minimum=5,
        maximum=43_200,
    ),
    ConfigKey(
        "auth_rate_limit_per_minute",
        Cat.auth,
        "int",
        "Per-IP requests per minute allowed on login/register.",
        minimum=1,
        maximum=1000,
    ),
    ConfigKey(
        "expensive_rate_limit_per_minute",
        Cat.auth,
        "int",
        "Per-user requests per minute on endpoints that cost money or capacity "
        "(model calls, sandbox work, deploys). 0 disables.",
        minimum=0,
        maximum=1000,
    ),
    ConfigKey(
        "max_request_bytes",
        Cat.auth,
        "int",
        "Hard backstop on any single request body. Must exceed a full design-image batch.",
        restart_required=True,
        minimum=1_048_576,
        maximum=1_073_741_824,
    ),
    ConfigKey(
        "secret_key",
        Cat.auth,
        "str",
        "Access-token signing key. Rotating it signs every user out, including you. "
        "Stored encrypted; never displayed again.",
        sensitive=True,
    ),
    ConfigKey(
        "seed_admin_email",
        Cat.auth,
        "str",
        "Optional dev admin created/promoted by `make seed`. Blank skips seeding.",
        extra=_email_or_blank,
    ),
    ConfigKey(
        "seed_admin_password",
        Cat.auth,
        "str",
        "Password for the seeded admin. Stored encrypted; never displayed again.",
        sensitive=True,
    ),
    ConfigKey(
        "demo_email",
        Cat.auth,
        "str",
        "Account seeded by the demo script (phase-50).",
        extra=_email_or_blank,
    ),
    ConfigKey(
        "demo_password",
        Cat.auth,
        "str",
        "Password for the demo account — blank means the script refuses to seed rather than "
        "shipping a known credential. Stored encrypted; never displayed again.",
        sensitive=True,
    ),
    # ---------------------------------------------------------------- core
    ConfigKey(
        "forgeflow_env",
        Cat.core,
        "str",
        "Deployment environment label reported by /health (local, staging, prod…).",
    ),
    ConfigKey(
        "app_version", Cat.core, "str", "Version string reported by the API.", restart_required=True
    ),
    ConfigKey(
        "cors_origins",
        Cat.core,
        "str",
        "Comma-separated allowed browser origins ('*' permitted in dev).",
        restart_required=True,
    ),
    ConfigKey(
        "log_level",
        Cat.core,
        "enum",
        "Minimum log level for the control plane.",
        restart_required=True,
        choices=LOG_LEVELS,
    ),
    ConfigKey(
        "fernet_key",
        Cat.core,
        "str",
        "Encryption key for the credential vault and for secrets stored here.",
        sensitive=True,
        locked=True,
        locked_reason=BOOTSTRAP_SECRET,
    ),
    ConfigKey(
        "metrics_path",
        Cat.core,
        "str",
        "Path the Prometheus exposition endpoint is served on. Must start with '/'.",
        restart_required=True,
        extra=_leading_slash,
    ),
    # ---------------------------------------------------------------- feature flags
    ConfigKey(
        "log_json",
        Cat.feature_flags,
        "bool",
        "Emit structured JSON logs (off = human-readable console logs).",
        restart_required=True,
    ),
    ConfigKey(
        "metrics_enabled",
        Cat.feature_flags,
        "bool",
        "Expose Prometheus metrics and time every request. Off removes both the endpoint and the "
        "measurement overhead.",
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

    Raises ``UserError`` (surfaced as 400 with detail) on any type/range/enum/locked violation.
    The returned value is already the correct Python type, so the resolver serves it without
    re-coercion. Sensitive values are returned as-is; encrypting them is ``config_admin``'s job.
    """
    entry = get_registered(key)
    if entry.locked:
        raise UserError(
            f"{key!r} cannot be set from the admin panel — {entry.locked_reason}",
            detail={"key": key, "locked": True},
        )

    value = _coerce(entry, raw)
    if entry.sensitive and not str(value).strip():
        raise UserError(
            f"{key!r} must not be blank — use Reset to fall back to the environment",
            detail={"key": key, "sensitive": True},
        )
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
