"""Stitch failure classification (phase-17).

Every Stitch failure is a :class:`ProviderError` carrying a ``kind`` (``detail["kind"]``) and a
``fallback_hint``. Classifying auth vs quota vs transient vs fatal lets phase-19/48 pick the right
graceful-degradation path (retry, refresh, or fail over to figma/fake) instead of a dead end.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from app.core.errors import ProviderError
from app.design.base import provider_error

PROVIDER_KEY = "stitch"


class StitchErrorKind(StrEnum):
    auth = "auth"  # credentials missing / rejected / token refresh failed
    quota = "quota"  # generation cap reached for the window
    transient = "transient"  # timeout / 5xx / network — worth a retry
    fatal = "fatal"  # we rejected the request locally — do not retry, do not fall back
    # Stitch rejected our call, or answered in a shape we cannot read. Retrying is pointless, but
    # another provider may well succeed, so this DOES advance the fallback chain (phase-48).
    contract = "contract"
    unsupported = "unsupported"  # the API has no such capability (e.g. screenshot→UI)
    # The provider answered with a question rather than a design, and the transport has already
    # spent its blind-confirmation budget on it. Not a failure: another provider cannot answer it
    # either, so this does NOT advance the fallback chain (see resilient.py) — it is handed to the
    # design stage, which parks it as a DesignQuestion for the user to answer.
    clarification = "clarification"


_DEFAULT_HINTS: dict[StitchErrorKind, str] = {
    StitchErrorKind.auth: (
        "Stitch credentials are missing or invalid. Add/rotate STITCH_* creds in settings, "
        "or switch the design provider to 'figma' or 'fake' (per project or globally)."
    ),
    StitchErrorKind.quota: (
        "Stitch's monthly generation quota is exhausted. Switch this project to 'figma' or 'fake', "
        "or wait for the quota window to reset — every stage is skippable."
    ),
    StitchErrorKind.transient: (
        "Stitch is temporarily unavailable. Retry shortly, or switch to 'figma'/'fake'."
    ),
    StitchErrorKind.fatal: (
        "Stitch could not process this request. Try a different input, or switch to 'figma'/'fake'."
    ),
    StitchErrorKind.contract: (
        "Stitch rejected the call or answered unexpectedly — its API may have changed. Run "
        "`uv run python -m scripts.stitch_probe` to see the tools it currently exposes; meanwhile "
        "the design falls back to 'figma'/'fake'."
    ),
    StitchErrorKind.clarification: (
        "Stitch needs a decision before it can design this. Answer its question in the design "
        "stage, or design without Stitch to fall back to 'figma'/'fake'."
    ),
    StitchErrorKind.unsupported: (
        "Stitch's API cannot do this (its MCP surface is text-prompt only). Use a text prompt, "
        "or switch the design provider to 'fake' for screenshot intake."
    ),
}


def stitch_error(
    kind: StitchErrorKind,
    message: str,
    *,
    detail: Any | None = None,
    hint: str | None = None,
) -> ProviderError:
    """Build a classified Stitch :class:`ProviderError` (kind + fallback hint)."""
    body: dict[str, Any] = {"kind": str(kind)}
    if detail is not None:
        body["detail"] = detail
    return provider_error(
        PROVIDER_KEY,
        message,
        detail=body,
        hint=hint or _DEFAULT_HINTS[kind],
    )
