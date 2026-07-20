"""Deploy failure classification (phase-35).

Mirrors the design-provider taxonomy (phase-17): every failure is a :class:`ProviderError` carrying
a ``kind`` and a ``fallback_hint``, so the orchestrator (phase-37) can retry, escalate, or report a
*partial* deploy honestly instead of hitting a dead end. ``detail`` also carries the ``target``
(fe/be) — that is what lets phase-37 say "the frontend is live, the backend failed".
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from app.core.errors import ProviderError


class DeployErrorKind(StrEnum):
    auth = "auth"  # token missing / rejected by the provider
    config = "config"  # the spec is not deployable (bad target, missing command, …)
    quota = "quota"  # plan/rate limit reached
    transient = "transient"  # timeout / 5xx / network — worth a retry
    fatal = "fatal"  # provider rejected the request — do not retry


_DEFAULT_HINTS: dict[DeployErrorKind, str] = {
    DeployErrorKind.auth: (
        "The deploy provider rejected the credentials. Add or replace the token in Settings → "
        "Provider credentials, or switch this deploy to seamless mode to use the platform's."
    ),
    DeployErrorKind.config: (
        "This project cannot be deployed as configured. Re-run the infra analysis on the deploy "
        "stage and confirm the detected build/start commands."
    ),
    DeployErrorKind.quota: (
        "The deploy provider's plan limit was reached. Use your own account (BYO credentials in "
        "Settings), remove unused deployments, or retry once the limit window resets."
    ),
    DeployErrorKind.transient: (
        "The deploy provider is temporarily unavailable. Retry shortly — nothing was lost."
    ),
    DeployErrorKind.fatal: (
        "The deploy provider could not process this deployment. Check the build output, then retry."
    ),
}


def deploy_error(
    provider: str,
    kind: DeployErrorKind,
    message: str,
    *,
    target: str | None = None,
    detail: Any | None = None,
    hint: str | None = None,
) -> ProviderError:
    """Build a classified deploy :class:`ProviderError` (provider + kind + target + hint)."""
    body: dict[str, Any] = {"provider": provider, "kind": str(kind)}
    if target is not None:
        body["target"] = str(target)
    if detail is not None:
        body["detail"] = detail
    return ProviderError(message, detail=body, fallback_hint=hint or _DEFAULT_HINTS[kind])


def kind_of(exc: ProviderError) -> DeployErrorKind | None:
    """Read the classification back off a deploy error (used by the retry loop)."""
    detail = exc.detail
    if not isinstance(detail, dict):
        return None
    try:
        return DeployErrorKind(str(detail.get("kind")))
    except ValueError:
        return None


__all__ = ["DeployErrorKind", "deploy_error", "kind_of"]
