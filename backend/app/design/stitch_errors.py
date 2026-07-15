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
    fatal = "fatal"  # bad request / unexpected response — do not retry


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
