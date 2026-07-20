"""Deploy provider adapters (phase-35, D11).

The split is fixed and non-negotiable: **the frontend goes to Vercel, the backend goes to Render.**
A persistent Node service on Vercel's serverless model is the failure mode §12 calls out, so it is
made *unrepresentable* here rather than merely discouraged — each provider serves exactly one
:class:`~app.deploy.providers.base.DeployTarget`, and :func:`provider_for` is the only lookup.
"""

from __future__ import annotations

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

#: The one provider that serves each target. There is deliberately no way to add a second.
_BY_TARGET: dict[DeployTarget, type[DeployProvider]] = {
    DeployTarget.fe: VercelDeployProvider,
    DeployTarget.be: RenderDeployProvider,
}


def provider_for(target: DeployTarget) -> DeployProvider:
    """The adapter for ``target``. Frontend → Vercel, backend → Render; never anything else."""
    return _BY_TARGET[DeployTarget(target)]()


__all__ = [
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
    "deploy_error",
    "provider_for",
]
