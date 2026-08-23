"""Build-failure classification (phase-55 task 9).

The user's explicit requirement: *"if the terminal gives any code-based error it will be sent to the
agent; if it is a sandbox env error it will not be forwarded — it will stop."* This module is what
makes that structurally true. A build step's output/exit is classified **before** any synthetic
`TestRun` is built, so an ``environment`` verdict cannot reach the repair loop — the input it would
need is never assembled.

Modelled on :func:`app.orchestrator.stages.validate.diagnose` and the ``DeployErrorKind`` idiom
(:mod:`app.deploy.providers.errors`): a small enum + a frozen diagnosis carrying an actionable hint.

**Erring toward ``code``** is deliberate and mirrors ``validate.diagnose``: misreading a real bug as
an env problem skips a repair the loop could have made, which is worse than one wasted iteration on
a misread env failure. So only recognised env signatures classify as ``environment``; everything
else is ``code`` and repairable.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.core.errors import ProviderError, SystemError, UserError  # noqa: A004 - taxonomy name
from app.sandbox.exec import EXIT_TIMEOUT
from app.sandbox.runtime import EXIT_UNKNOWN


class BuildFailureKind(StrEnum):
    code = "code"  # a real product bug → the bounded repair loop can act on it
    environment = "environment"  # the sandbox/infra is broken → a code patch cannot fix it
    provider = "provider"  # the model provider failed (quota/auth/transient)
    budget = "budget"  # a spend cap halted the run


@dataclass(frozen=True)
class BuildDiagnosis:
    """A classified build failure: what kind, why, the matched signal, and how to fix it."""

    kind: BuildFailureKind
    reason: str
    signal: str
    hint: str

    @property
    def repairable(self) -> bool:
        """Only ``code`` failures are ever forwarded to the repair loop."""
        return self.kind is BuildFailureKind.code


# Each env class: the substrings that identify it (matched case-insensitively against the combined
# stdout/stderr tail), a human reason, and an actionable hint. Ordered most-specific first — the
# first class with any matching signal wins, so a dead container is named before a generic timeout.
_ENV_CLASSES: tuple[tuple[str, tuple[str, ...], str], ...] = (
    (
        "the sandbox container is not running",
        (
            "oci runtime exec failed",
            "procready not received",
            "container not running",
            "no such container",
        ),
        "The sandbox container died mid-build — it was killed, ran out of resources, or never "
        "started — not a code problem. Check the host's docker/Colima resources, then rebuild.",
    ),
    (
        "a process ran out of memory (OOM-killed)",
        ("javascript heap out of memory", "oomkilled", "signal sigkill"),
        "A build process was killed for using too much memory. Raise the sandbox memory limit "
        "(SANDBOX_MEM_LIMIT) or reduce the build's memory use, then rebuild.",
    ),
    (
        "the sandbox hit its process limit",
        (
            "fork: resource temporarily unavailable",
            "spawn enomem",
            "cannot fork",
            # Node does not fail gracefully at the PID ceiling: it cannot start its own libuv
            # threadpool and aborts with a native assertion that mentions neither PIDs nor limits.
            # Without this it reads like a code crash and the repair loop burns iterations on it
            # (phase-59).
            "uv_thread_create",
            "workerthreadstaskrunner",
        ),
        "The sandbox reached its process limit (SANDBOX_PIDS_LIMIT). Raise it, or avoid spawning "
        "extra dev servers during the build, then rebuild.",
    ),
    (
        "a preview port was already in use",
        ("eaddrinuse", "is in use, trying another one"),
        "A dev server could not take its assigned port because an orphaned process still held it. "
        "Restart the preview (ports are reclaimed on start) or restart the sandbox, then rebuild.",
    ),
    (
        "the sandbox ran out of disk space",
        ("enospc", "no space left on device"),
        "The sandbox ran out of disk space. Free space or grow the workspace volume, then rebuild.",
    ),
    (
        "the package registry was unreachable",
        ("getaddrinfo enotfound registry", "eai_again", "err_pnpm_meta_fetch_fail"),
        "The package registry was unreachable from the sandbox. Check the egress network and "
        "registry availability, then rebuild — no code change fixes a network failure.",
    ),
    (
        "the app database was unreachable",
        ("buffering timed out after",),
        "The app's database was unreachable — its queries buffered and timed out. Point the app DB "
        "URI at a database the sandbox can reach, then rebuild.",
    ),
)

_TIMEOUT_REASON = "the build step exceeded its time budget"
_TIMEOUT_HINT = (
    "A build step ran past its time budget — a slow sandbox or a hung process, not necessarily a "
    "bug. Raise the step's timeout or investigate the host, then rebuild."
)
_CONTAINER_DEAD_REASON = "the sandbox container is not running"
_CONTAINER_DEAD_HINT = _ENV_CLASSES[0][2]


def _env(reason: str, signal: str, hint: str) -> BuildDiagnosis:
    return BuildDiagnosis(BuildFailureKind.environment, reason, signal, hint)


def classify_output(
    text: str,
    *,
    exit_code: int | None = None,
    timed_out: bool = False,
    container_alive: bool | None = None,
) -> BuildDiagnosis:
    """Classify a build step from its output + exit signals.

    Returns ``environment`` for any recognised sandbox/infra failure and ``code`` otherwise. Never
    returns ``provider``/``budget`` — those come only from exceptions (:func:`classify_exception`).
    """
    # An independent liveness signal beats any exit code: a dead container's ExitCode is unreliable.
    if container_alive is False:
        return _env(_CONTAINER_DEAD_REASON, "container_alive is False", _CONTAINER_DEAD_HINT)
    if exit_code == EXIT_UNKNOWN:
        return _env(
            _CONTAINER_DEAD_REASON, "exit code unreadable (container died)", _CONTAINER_DEAD_HINT
        )

    lowered = text.lower()

    # OOM and dead-container also show as numeric exit signals, checked before the generic timeout.
    if exit_code in (137, -9):
        return _env(_ENV_CLASSES[1][0], f"exit {exit_code}", _ENV_CLASSES[1][2])

    for reason, signals, hint in _ENV_CLASSES:
        for signal in signals:
            if signal in lowered:
                return _env(reason, signal, hint)

    if timed_out or exit_code == EXIT_TIMEOUT:
        signal = "timed_out" if timed_out else f"exit {EXIT_TIMEOUT}"
        return _env(_TIMEOUT_REASON, signal, _TIMEOUT_HINT)

    # The conservative default (mirrors validate.diagnose): treat it as a real bug the loop can fix.
    return BuildDiagnosis(
        BuildFailureKind.code,
        "the failure names application behaviour, so the repair loop can act",
        "default",
        "",
    )


def classify_exception(exc: Exception) -> BuildDiagnosis:
    """Classify an exception raised during a build step.

    ``ProviderError`` → ``provider``; a budget ``UserError`` → ``budget``; a ``SystemError`` (the
    taxonomy's infra class) → ``environment``. Anything else errs toward ``code`` so an unexpected
    failure still gets a repair attempt rather than silently stopping.
    """
    if isinstance(exc, ProviderError):
        hint = getattr(exc, "fallback_hint", "") or (
            "The model provider failed (quota, auth, or a transient error). Retry shortly or check "
            "the provider credentials in Settings."
        )
        return BuildDiagnosis(
            BuildFailureKind.provider, "the model provider failed", str(exc), hint
        )

    message = str(exc)
    if isinstance(exc, UserError) and "budget" in message.lower():
        return BuildDiagnosis(
            BuildFailureKind.budget,
            "a spend cap halted the build",
            message,
            "The project or platform budget cap was reached. Raise the cap or wait for the window "
            "to reset, then rebuild.",
        )

    if isinstance(exc, SystemError):
        return _env(
            "a sandbox/infrastructure error stopped the build",
            message,
            "The sandbox or its runtime was unavailable. Check docker/Colima and the control "
            "plane, then rebuild — this is not a code problem.",
        )

    return BuildDiagnosis(
        BuildFailureKind.code,
        "an unexpected error — treating it as repairable",
        message,
        "",
    )


__all__ = [
    "BuildDiagnosis",
    "BuildFailureKind",
    "classify_exception",
    "classify_output",
]
