"""Figma failure classification (phase-18).

Mirrors the Stitch taxonomy but swaps quota (Figma has no per-generation cap) for **unsupported** —
Figma genuinely can't do some things Stitch can (e.g. screenshot→UI). Those degrade with a clear
message instead of faking a result (D9 design note: pluggability includes capability differences).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from app.core.errors import ProviderError
from app.design.base import provider_error

PROVIDER_KEY = "figma"


class FigmaErrorKind(StrEnum):
    auth = "auth"  # token missing / rejected
    unsupported = "unsupported"  # capability Figma does not offer (honest degrade, not a crash)
    transient = "transient"  # timeout / 5xx / rate-limit — worth a retry
    fatal = "fatal"  # bad request / unexpected response — do not retry


_DEFAULT_HINTS: dict[FigmaErrorKind, str] = {
    FigmaErrorKind.auth: (
        "The Figma token is missing or invalid. Add/rotate FIGMA_TOKEN in settings, or switch the "
        "design provider to 'stitch' or 'fake'."
    ),
    FigmaErrorKind.unsupported: (
        "Figma doesn't support this operation. Use a supported input, or switch this project to "
        "'stitch' for that capability."
    ),
    FigmaErrorKind.transient: (
        "Figma is temporarily unavailable. Retry shortly, or switch to 'stitch'/'fake'."
    ),
    FigmaErrorKind.fatal: (
        "Figma could not process this request. Try a different input, or switch to 'stitch'/'fake'."
    ),
}


def figma_error(
    kind: FigmaErrorKind,
    message: str,
    *,
    detail: Any | None = None,
    hint: str | None = None,
) -> ProviderError:
    """Build a classified Figma :class:`ProviderError` (kind + fallback hint)."""
    body: dict[str, Any] = {"kind": str(kind)}
    if detail is not None:
        body["detail"] = detail
    return provider_error(PROVIDER_KEY, message, detail=body, hint=hint or _DEFAULT_HINTS[kind])
