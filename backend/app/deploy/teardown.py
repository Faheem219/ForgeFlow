"""Deployment teardown (phase-62) — take a deployment down and leave the project honest.

``DeployProvider.destroy`` has existed since phase-35 with no caller: nothing in ForgeFlow could
remove a deployment, and deleting a *project* left its deployments live and billing forever. This is
that caller, used by both the explicit "delete deployment" action and the project-delete cascade.

Three properties matter:

- **Fail soft, report loudly.** A revoked token or an already-deleted deployment must not leave the
  user with a record they cannot remove. Each target is attempted independently and whatever could
  not be reclaimed is named in the report, exactly as :class:`~app.projects.service.DeleteReport`
  does for sandboxes and databases.
- **Hosting only, never data.** The app database is untouched. Dropping data stays an explicit
  project-delete opt-in (D8) — taking a site down is not a reason to destroy what it stored.
- **The stage stops claiming a live deployment.** ``validate ⇐ deploy`` is a hard prereq (D12), so
  leaving deploy ``complete`` after a teardown would let live-validation run against a URL that no
  longer resolves. Deploy goes back to ``empty`` and validate — whose evidence now points at a dead
  URL — goes ``stale``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from beanie import PydanticObjectId

from app.db.models import STATUS_DELETED, Deployment, DeploymentRef
from app.db.models.enums import Stage, StageStatus
from app.db.repos import StageStateRepo
from app.deploy.providers import DeployProvider, DeployTarget, provider_for
from app.deploy.providers.base import DeployRef
from app.realtime.hub import emit
from app.realtime.schemas import EventType

logger = logging.getLogger(__name__)


@dataclass
class TeardownReport:
    """What a teardown actually reclaimed. ``warnings`` name what outlived it."""

    #: Target keys whose provider deployment is gone ("fe", "be").
    destroyed: list[str] = field(default_factory=list)
    #: True once the record is marked deleted — which happens even if a provider refused.
    record_deleted: bool = False
    warnings: list[str] = field(default_factory=list)


class DeploymentTeardown:
    def __init__(
        self, provider_factory: Callable[[DeployTarget], DeployProvider] | None = None
    ) -> None:
        # Resolved here rather than as a default argument: a default binds `provider_for` at import
        # time, which freezes the seam shut for anything that swaps it later.
        self._provider_factory = provider_factory or provider_for
        self._stages = StageStateRepo()

    async def destroy(
        self, deployment: Deployment, *, user_id: PydanticObjectId | None = None
    ) -> TeardownReport:
        """Destroy every provider deployment on ``deployment``, then retire the record."""
        report = TeardownReport()

        for key, ref in sorted(deployment.refs.items()):
            try:
                provider = self._provider_factory(DeployTarget(key))
                await provider.destroy(
                    _to_deploy_ref(key, ref), mode=deployment.mode, user_id=user_id
                )
                report.destroyed.append(key)
            except Exception as exc:  # noqa: BLE001 - teardown must never block the delete
                report.warnings.append(f"{key}: {exc}")
                logger.warning(
                    "deployment teardown failed for one target",
                    extra={"target": key, "deployment_id": str(deployment.id)},
                    exc_info=True,
                )

        # The record is retired whatever the providers said. A deployment ForgeFlow can no longer
        # manage is not one it should keep advertising as live; the warnings say what to clean up
        # by hand.
        deployment.status = STATUS_DELETED
        deployment.urls = {}
        deployment.refs = {}
        deployment.topology_snapshot = _cleared(deployment.topology_snapshot)
        await deployment.save()
        report.record_deleted = True

        await self._reset_stages(deployment.project_id)
        await emit(
            str(deployment.project_id),
            EventType.deploy_status,
            {
                "step": "health",
                "status": STATUS_DELETED,
                "deployment_id": str(deployment.id),
                "destroyed": report.destroyed,
                "warnings": report.warnings,
            },
            stage=Stage.deploy,
        )
        return report

    # -- internals ---------------------------------------------------------------------------

    async def _reset_stages(self, project_id: PydanticObjectId) -> None:
        """Deploy is no longer done, and validate's evidence points at a URL that is gone."""
        await self._stages.set_status(project_id, Stage.deploy, StageStatus.empty)
        validate = await self._stages.get_or_create(project_id, Stage.validate)
        if validate.status is not StageStatus.empty:
            await self._stages.set_status(project_id, Stage.validate, StageStatus.stale)


def _to_deploy_ref(key: str, ref: DeploymentRef) -> DeployRef:
    return DeployRef(id=ref.id, project=ref.project, target=DeployTarget(key))


def _cleared(snapshot: dict[str, object]) -> dict[str, object]:
    """The same topology with every node's live URL/status dropped — shape kept, liveness gone."""
    nodes = snapshot.get("nodes")
    if not isinstance(nodes, list):
        return snapshot
    return {
        **snapshot,
        "nodes": [
            {**node, "url": None, "status": STATUS_DELETED} if isinstance(node, dict) else node
            for node in nodes
        ],
        "status": STATUS_DELETED,
    }


__all__ = ["DeploymentTeardown", "TeardownReport"]
