"""Shared helpers + embedded (non-document) models."""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    """Timezone-aware UTC now (used as the default_factory for timestamps)."""
    return datetime.now(UTC)
