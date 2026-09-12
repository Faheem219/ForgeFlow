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
  guides the user to switch). The corollary is :func:`provider_for_refine`: a refine must go to the
  provider that *produced* the design, which after a fallback is **not** the active one.
- **``fatal`` and ``clarification`` never fall back.** A request *we* rejected locally is the
  caller's problem; another provider will reject it too. A ``clarification`` is not a failure at
  all — the provider is waiting on a decision only the user can make, and quietly asking a
  different provider to design something else is exactly the outcome this avoids (it goes to the
  design stage, which parks it: ``app/design/questions.py``). Everything else —
  quota/auth/transient/unsupported/``contract`` (the provider rejected our call or drifted)
  /unknown — advances the chain.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass, field
from enum import StrEnum

from app.core.config import get_config
from app.core.errors import ProviderError, UserError
from app.db.models import Project
from app.design.base import DesignProvider, DesignResult, provider_error
from app.design.registry import get_provider, resolve_active_key

logger = logging.getLogger(__name__)

#: Error kinds that must not trigger a fallback: the request itself is bad (``fatal``), or the
#: provider is waiting on the user rather than failing (``clarification``).
_NON_RECOVERABLE = frozenset({"fatal", "clarification"})


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


def candidate_keys(
    project: Project | None,
    capability: DesignCapability,
    *,
    exclude: Collection[str] = (),
) -> list[str]:
    """The ordered, deduped, capability-filtered provider keys to try for ``capability``.

    Active provider first (honoring the per-project override), then the configured fallback chain.
    A key that is unregistered or cannot do ``capability`` is dropped rather than attempted.

    ``exclude`` drops a provider the caller has already decided against — how "design without
    Stitch" answers a clarifying question the user does not want to answer: the provider that is
    waiting on them is skipped and the chain serves the design instead.
    """
    ordered: list[str] = []
    for key in [resolve_active_key(project), *_fallback_chain()]:
        if key not in ordered and key not in exclude:
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


@dataclass(frozen=True)
class RefineTarget:
    """The provider a refine must be sent to, plus what to tell the user about that choice."""

    provider: DesignProvider
    key: str
    #: Set when the refine is not going to the active provider (or could not follow provenance).
    note: str | None = None


def provider_for_refine(project: Project | None, produced_by: str | None) -> RefineTarget:
    """The provider that **produced** the design being refined — not the active one.

    An ``external_ref`` only means something to its own provider. Sending one elsewhere gets a
    confusing not-found from the far end: a design generated by the ``fake`` fallback (because
    Stitch was down or out of quota) refined against a still-active ``stitch`` produced exactly
    that — *"Stitch tool error: Requested entity was not found"* on a ``fake-text-…`` ref. So
    refine follows the artifact's recorded provider.

    An unknown or missing provenance key falls back to the active provider (the best guess for a
    legacy artifact) with a note, rather than blocking the refine.
    """
    active = resolve_active_key(project)
    key = (produced_by or "").strip()
    if not key:
        return RefineTarget(get_provider(active), active)
    if key == active:
        return RefineTarget(get_provider(active), active)
    try:
        provider = get_provider(key)
    except UserError:
        return RefineTarget(
            get_provider(active),
            active,
            note=(
                f"This design was produced by {key!r}, which is not registered — "
                f"refined via {active} instead, which may not recognise the design."
            ),
        )
    return RefineTarget(
        provider,
        key,
        note=(
            f"Refined via {key} because it produced this design (the active provider is "
            f"{active}). Generate a new design to move this project onto {active}."
        ),
    )


def _reason(exc: ProviderError) -> str:
    detail = exc.detail
    kind = detail.get("kind") if isinstance(detail, dict) else None
    known = {
        "quota": "quota is exhausted",
        "auth": "credentials are missing or invalid",
        "transient": "is temporarily unavailable",
        "unsupported": "doesn't support this",
        "contract": "rejected the request",
        "down": "is unavailable",
        "clarification": "needs a decision from you",
    }
    return known.get(str(kind), "was unavailable")


def _is_recoverable(exc: ProviderError) -> bool:
    detail = exc.detail
    kind = detail.get("kind") if isinstance(detail, dict) else None
    return str(kind) not in _NON_RECOVERABLE


async def invoke_with_fallback(
    project: Project | None,
    capability: DesignCapability,
    call: DesignCall,
    *,
    exclude: Collection[str] = (),
) -> DesignOutcome:
    """Run ``call`` against each candidate until one succeeds.

    Raises the **last** :class:`ProviderError` (with its ``fallback_hint``) if every candidate
    fails, or re-raises immediately on a non-recoverable (``fatal``/``clarification``) one.
    """
    keys = candidate_keys(project, capability, exclude=exclude)
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
                # The user-facing note is deliberately short ("stitch rejected the request"), so
                # the provider's own message is only ever seen here — log it, or a fallback is
                # undiagnosable after the fact.
                logger.warning(
                    "design provider %s failed (%s); falling back: %s",
                    key,
                    reasons[key],
                    exc.message,
                )
            continue
        return DesignOutcome(result=result, provider_key=key, attempted=attempted, reasons=reasons)

    assert last_error is not None  # keys was non-empty, so at least one attempt ran
    raise last_error


__all__ = [
    "DesignCapability",
    "DesignOutcome",
    "RefineTarget",
    "candidate_keys",
    "invoke_with_fallback",
    "provider_for_refine",
]
