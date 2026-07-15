"""The DesignProvider contract (phase-16).

Every design backend (Stitch phase-17, Figma phase-18, the fake below) implements this Protocol.
Consumers depend on *this module only* — that is what makes provider selection a config change
(D9) rather than a code change.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, Field

from app.core.errors import ProviderError


class ProviderHealth(StrEnum):
    ok = "ok"
    degraded = "degraded"  # reachable but limited (e.g. quota nearly exhausted)
    down = "down"


@dataclass(frozen=True)
class DesignImage:
    """An input screenshot/mock handed to a provider."""

    filename: str
    media_type: str  # e.g. image/png
    data: bytes


class DesignCapabilities(BaseModel):
    """What a provider can actually do — lets the UI hide affordances it cannot honor."""

    provider: str
    from_image: bool = False
    from_text: bool = False
    refine: bool = False
    fetch_code: bool = False
    max_images: int = 0


class DesignResult(BaseModel):
    """A generated design. ``external_ref`` is the provider's handle for later fetch/refine."""

    provider: str
    external_ref: str
    html: str
    css: str
    preview_image: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class DesignCode(BaseModel):
    html: str
    css: str
    assets: dict[str, str] = Field(default_factory=dict)  # name → url/ref


class DesignProvider(Protocol):
    """A design backend. ``key`` is the registry/config name (``stitch``/``figma``/``fake``)."""

    key: str

    async def generate_from_image(
        self, images: list[DesignImage], prompt: str | None = None
    ) -> DesignResult: ...

    async def generate_from_text(self, prompt: str) -> DesignResult: ...

    async def fetch_code(self, design_ref: str) -> DesignCode: ...

    async def refine(self, design_ref: str, instruction: str) -> DesignResult: ...

    def capabilities(self) -> DesignCapabilities: ...

    async def health(self) -> ProviderHealth: ...


def provider_error(
    provider: str,
    message: str,
    *,
    detail: Any | None = None,
    hint: str | None = None,
) -> ProviderError:
    """Build the standard :class:`ProviderError` for a design provider failure.

    Always carries a ``fallback_hint``: design providers are the most failure-prone dependency
    (D10 — Stitch quota/auth), and phase-48 turns these hints into graceful degradation instead
    of a dead end.
    """
    return ProviderError(
        message,
        detail=detail,
        fallback_hint=hint
        or (
            f"The '{provider}' design provider is unavailable. Switch providers in settings "
            f"(or per project), or continue without a design — every stage is skippable."
        ),
    )
