"""Automatic design-provider fallback (phase-48, D10 / risk §12).

Design providers are the most failure-prone dependency ForgeFlow has — Stitch's monthly generation
quota is a *when*, not an *if*. Earlier phases classified every provider failure as a
:class:`ProviderError` with a ``fallback_hint``; this module turns that hint into action.

Rather than dead-ending, a **generate** call tries the active provider first and then a configured
chain (``figma`` → ``fake`` by default), skipping providers that lack the needed capability, and
reports which provider actually served the request so the stage can tell the user a fallback
happened ("Stitch's quota is exhausted — generated via figma instead").

Two deliberate boundaries:

- **Generation only.** ``refine`` keys off a provider-specific ``external_ref``, so handing it to a
  different provider would fail anyway — refine stays single-provider (its ``fallback_hint`` still
  guides the user to switch).
- **``fatal`` never falls back.** A malformed request is the *caller's* problem; another provider
  will reject it too. Everything else (quota/auth/transient/unsupported/unknown) advances the chain.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum

from app.core.config import get_config
from app.core.errors import ProviderError, UserError
from app.db.models import Project
from app.design.base import DesignProvider, DesignResult, provider_error
from app.design.registry import get_provider, resolve_active_key

logger = logging.getLogger(__name__)

#: The one error kind that must not trigger a fallback — the request itself is bad.
_NON_RECOVERABLE = "fatal"


class DesignCapability(StrEnum):
    """Generate capabilities a fallback candidate is filtered on (maps to ``capabilities()``)."""

    from_text = "from_text"
    from_image = "from_image"


DesignCall = Callable[[DesignProvider], Awaitable[DesignResult]]


@dataclass(frozen=True)
class DesignOutcome:
    """A served design plus the provenance the stage surfaces to the user."""

    result: DesignResult
    provider_key: str
    #: Providers tried in order; the last is :attr:`provider_key`.
    attempted: list[str] = field(default_factory=list)
    #: Why each non-serving provider was skipped (key → short reason), for the user message.
    reasons: dict[str, str] = field(default_factory=dict)

    @property
    def fell_back(self) -> bool:
        return bool(self.attempted) and self.attempted[0] != self.provider_key

    def note(self) -> str | None:
        """A one-line explanation when a fallback occurred, else ``None``."""
        if not self.fell_back:
            return None
        first = self.attempted[0]
        reason = self.reasons.get(first, "was unavailable")
        return f"{first} {reason} — generated via {self.provider_key} instead."


def _supports(provider: DesignProvider, capability: DesignCapability) -> bool:
    return bool(getattr(provider.capabilities(), str(capability), False))


def _fallback_chain() -> list[str]:
    raw = str(get_config().get("design_fallback_chain"))
    return [key.strip() for key in raw.split(",") if key.strip()]


def candidate_keys(project: Project | None, capability: DesignCapability) -> list[str]:
    """The ordered, deduped, capability-filtered provider keys to try for ``capability``.

    Active provider first (honoring the per-project override), then the configured fallback chain.
    A key that is unregistered or cannot do ``capability`` is dropped rather than attempted.
    """
    ordered: list[str] = []
    for key in [resolve_active_key(project), *_fallback_chain()]:
        if key not in ordered:
            ordered.append(key)

    usable: list[str] = []
    for key in ordered:
        try:
            provider = get_provider(key)
        except UserError:
            continue  # a stale config/override name — skip, don't crash
        if _supports(provider, capability):
            usable.append(key)
    return usable


def _reason(exc: ProviderError) -> str:
    detail = exc.detail
    kind = detail.get("kind") if isinstance(detail, dict) else None
    known = {
        "quota": "quota is exhausted",
        "auth": "credentials are missing or invalid",
        "transient": "is temporarily unavailable",
        "unsupported": "doesn't support this",
        "down": "is unavailable",
    }
    return known.get(str(kind), "was unavailable")


def _is_recoverable(exc: ProviderError) -> bool:
    detail = exc.detail
    kind = detail.get("kind") if isinstance(detail, dict) else None
    return str(kind) != _NON_RECOVERABLE


async def invoke_with_fallback(
    project: Project | None,
    capability: DesignCapability,
    call: DesignCall,
) -> DesignOutcome:
    """Run ``call`` against each candidate until one succeeds.

    Raises the **last** :class:`ProviderError` (with its ``fallback_hint``) if every candidate
    fails, or re-raises immediately on a non-recoverable (``fatal``) failure.
    """
    keys = candidate_keys(project, capability)
    if not keys:
        raise provider_error(
            resolve_active_key(project),
            "No design provider is configured to handle this request",
        )

    attempted: list[str] = []
    reasons: dict[str, str] = {}
    last_error: ProviderError | None = None

    for key in keys:
        provider = get_provider(key)
        attempted.append(key)
        try:
            result = await call(provider)
        except ProviderError as exc:
            last_error = exc
            reasons[key] = _reason(exc)
            if not _is_recoverable(exc):
                raise
            if key != keys[-1]:
                logger.info(
                    "design provider fell back",
                    extra={"from_provider": key, "reason": reasons[key]},
                )
            continue
        return DesignOutcome(result=result, provider_key=key, attempted=attempted, reasons=reasons)

    assert last_error is not None  # keys was non-empty, so at least one attempt ran
    raise last_error


__all__ = [
    "DesignCapability",
    "DesignOutcome",
    "candidate_keys",
    "invoke_with_fallback",
]
