"""Design provider registry + active-provider selection (phase-16).

Resolution order for the active provider:

    1. **per-project override** (``Project.design_provider``) — lets one project pin Figma when
       Stitch's quota is exhausted, without touching anyone else (D10 fail-soft).
    2. **admin / env / default** — via the layered config resolver (``design_provider``), so the
       admin dashboard (phase-51/52) can switch the platform default at runtime.

Real adapters self-register in their own phases (stitch → phase-17, figma → phase-18); nothing
here changes when they land.
"""

from __future__ import annotations

from app.core.config import get_config
from app.core.errors import UserError
from app.db.models import Project
from app.design.base import DesignProvider
from app.design.fake import FakeDesignProvider
from app.design.figma import FigmaDesignProvider
from app.design.stitch import StitchDesignProvider

_PROVIDERS: dict[str, DesignProvider] = {}


def register_provider(provider: DesignProvider) -> None:
    """Register (or replace) a provider under its ``key``."""
    _PROVIDERS[provider.key] = provider


def available_providers() -> list[str]:
    return sorted(_PROVIDERS)


def get_provider(key: str) -> DesignProvider:
    provider = _PROVIDERS.get(key)
    if provider is None:
        # Actionable rather than internal: the key comes from admin config or a project override,
        # both of which a human can correct.
        raise UserError(
            f"Unknown design provider: {key!r}. Available: {', '.join(available_providers())}"
        )
    return provider


def resolve_active_key(project: Project | None = None) -> str:
    """The provider key that *would* be used — project override first, then admin/env/default."""
    if project is not None and project.design_provider:
        return project.design_provider
    return str(get_config().get("design_provider"))


def get_active(project: Project | None = None) -> DesignProvider:
    return get_provider(resolve_active_key(project))


def reset_registry() -> None:
    """Restore the built-in registrations (test helper)."""
    _PROVIDERS.clear()
    _register_builtins()


def _register_builtins() -> None:
    # The fake is always available: it is the offline dev/demo fallback and the test default.
    register_provider(FakeDesignProvider())
    # Stitch is the default provider (D9). It registers unconditionally and self-reports
    # health()=down when creds are absent, so the stage falls back rather than crashing.
    register_provider(StitchDesignProvider())
    # Figma is the alternative + Stitch's fallback target (D10); same self-reports-down behavior.
    register_provider(FigmaDesignProvider())


_register_builtins()
