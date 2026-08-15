"""The DesignProvider contract (phase-16).

Every design backend (Stitch phase-17, Figma phase-18, the fake below) implements this Protocol.
Consumers depend on *this module only* — that is what makes provider selection a config change
(D9) rather than a code change.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

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
    #: Can enumerate every screen in the project it designs into (see :class:`ScreenLister`).
    list_screens: bool = False


class DesignScreen(BaseModel):
    """One screen of a multi-screen design (a provider may return a whole flow at once)."""

    id: str = ""
    title: str = ""
    html: str = ""
    preview_image: str | None = None


class DesignScreenRef(BaseModel):
    """A pointer to one screen that lives in the provider's project.

    A real app is a *set* of screens — landing, login, signup, dashboard — built up over several
    generate/refine turns. Each ForgeFlow design version records only the screen that turn produced,
    so listing what the provider actually holds is what lets the UI show the whole app and let the
    user pick which screen to look at, and refine, next.
    """

    #: The provider handle — usable directly with ``fetch_code()`` and ``refine()``.
    ref: str
    title: str = ""
    preview_image: str | None = None


class DesignResult(BaseModel):
    """A generated design. ``external_ref`` is the provider's handle for later fetch/refine.

    ``html``/``css`` are the **primary** screen — every consumer (preview, codegen context) can rely
    on them. ``screens`` carries the full set when a provider returned more than one, so the UI can
    offer a switcher without changing what single-screen consumers see.
    """

    provider: str
    external_ref: str
    html: str
    css: str
    preview_image: str | None = None
    screens: list[DesignScreen] = Field(default_factory=list)
    #: The provider-side container this design was created in (for Stitch, its project id). Recorded
    #: so a ForgeFlow project keeps designing into its **own** provider project instead of whichever
    #: one the shared provider instance last touched. Blank when the provider has no such notion.
    #:
    #: ``workspace=None`` on a generate means "this ForgeFlow project has no container yet — make
    #: one"; ``workspace_title`` names it, so the user can tell their projects apart provider-side
    #: instead of finding a stack of identically-named ones.
    workspace: str = ""
    meta: dict[str, Any] = Field(default_factory=dict)


class DesignCode(BaseModel):
    html: str
    css: str
    assets: dict[str, str] = Field(default_factory=dict)  # name → url/ref


class DesignProvider(Protocol):
    """A design backend. ``key`` is the registry/config name (``stitch``/``figma``/``fake``)."""

    key: str

    async def generate_from_image(
        self,
        images: list[DesignImage],
        prompt: str | None = None,
        *,
        workspace: str | None = None,
        workspace_title: str | None = None,
    ) -> DesignResult: ...

    async def generate_from_text(
        self,
        prompt: str,
        *,
        workspace: str | None = None,
        workspace_title: str | None = None,
    ) -> DesignResult: ...

    async def fetch_code(self, design_ref: str) -> DesignCode: ...

    async def refine(self, design_ref: str, instruction: str) -> DesignResult: ...

    def capabilities(self) -> DesignCapabilities: ...

    async def health(self) -> ProviderHealth: ...


@runtime_checkable
class ScreenLister(Protocol):
    """**Optional** provider capability: enumerate every screen in the provider's project.

    Deliberately separate from :class:`DesignProvider` so a provider that has no such notion
    (an imported design, the `fake` provider) is not forced to fake one. Callers check with
    ``isinstance(provider, ScreenLister)`` and simply offer no screen picker otherwise.

    ``workspace`` scopes the listing to one ForgeFlow project's provider container. It is not
    optional in spirit: without it a shared provider instance would happily list *another*
    project's screens, which is precisely the leak this parameter exists to prevent — so a
    provider that is given ``None`` must return nothing rather than guessing.
    """

    async def list_screens(self, workspace: str | None = None) -> list[DesignScreenRef]: ...


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
