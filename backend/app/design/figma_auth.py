"""Figma auth (phase-18) — a single static access token, no OAuth refresh (unlike Stitch).

The token comes from the layered config today; it moves behind the encrypted vault in phase-34.
**Never logged.**
"""

from __future__ import annotations

from app.core.config import get_config
from app.design.figma_errors import FigmaErrorKind, figma_error


def figma_token_present() -> bool:
    return bool(get_config().get("figma_token"))


def get_figma_token() -> str:
    """Return the configured Figma token, or raise an auth :class:`ProviderError` if absent."""
    token = str(get_config().get("figma_token"))
    if not token:
        raise figma_error(FigmaErrorKind.auth, "Figma token is not configured")
    return token
