"""Deploy provider adapters (phase-35; D11 amended in phase-58).

The frontend always goes to **Vercel**. The backend goes to Vercel too *by default* — it deploys
from the same inline file upload, so nothing has to push the workspace to a git host — and to
**Render** when ``DEPLOY_BE_PROVIDER=render``, which builds by cloning ``DEPLOY_REPO_URL``.

:func:`provider_for` remains the only lookup, and each adapter still declares exactly which targets
it serves (:attr:`~app.deploy.providers.base.BaseDeployProvider.served_targets`), so an unroutable
target fails at the boundary rather than reaching a transport that has no code path for it.
"""

from __future__ import annotations

from app.core.config import get_config
from app.deploy.providers.base import (
    DeployProvider,
    DeployRef,
    DeployResult,
    DeploySpec,
    DeployState,
    DeployTarget,
    LogLine,
)
from app.deploy.providers.errors import DeployErrorKind, deploy_error
from app.deploy.providers.render import RenderDeployProvider
from app.deploy.providers.vercel import VercelDeployProvider

#: Backend adapters, selected by ``DEPLOY_BE_PROVIDER``. The frontend has no such choice.
_BE_PROVIDERS: dict[str, type[DeployProvider]] = {
    "vercel": VercelDeployProvider,
    "render": RenderDeployProvider,
}

#: What a blank/unknown ``DEPLOY_BE_PROVIDER`` resolves to.
DEFAULT_BE_PROVIDER = "vercel"


def _backend_provider_key() -> str:
    key = str(get_config().get("deploy_be_provider")).strip().lower()
    return key if key in _BE_PROVIDERS else DEFAULT_BE_PROVIDER


def provider_for(target: DeployTarget) -> DeployProvider:
    """The adapter for ``target``. Frontend → Vercel; backend → ``DEPLOY_BE_PROVIDER``."""
    resolved = DeployTarget(target)
    if resolved is DeployTarget.fe:
        return VercelDeployProvider()
    return _BE_PROVIDERS[_backend_provider_key()]()


def backend_uploads_source() -> bool:
    """True when the backend target ships an inline file set rather than cloning a repo.

    The orchestrator needs this *before* building a spec: collecting the workspace costs sandbox
    commands, so it is only done when the provider will actually use the result.
    """
    return _backend_provider_key() == "vercel"


__all__ = [
    "DEFAULT_BE_PROVIDER",
    "DeployErrorKind",
    "DeployProvider",
    "DeployRef",
    "DeployResult",
    "DeploySpec",
    "DeployState",
    "DeployTarget",
    "LogLine",
    "RenderDeployProvider",
    "VercelDeployProvider",
    "backend_uploads_source",
    "deploy_error",
    "provider_for",
]
