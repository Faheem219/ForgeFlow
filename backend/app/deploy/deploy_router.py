"""Deploy read API (phase-38) — what the topology view hydrates from.

The graph follows live ``deploy.status`` events while a deploy runs, but a reload has no events to
replay, so it hydrates from the last persisted ``Deployment`` instead — which is why these are
read-only:

- ``GET /projects/{id}/deploy/latest`` — the most recent deployment (topology, URLs, status), or
  ``null`` before the first deploy.
- ``GET /projects/{id}/deploy/latest/logs`` — ForgeFlow's own **pipeline** log (which database mode
  was chosen, why the frontend was skipped, whether the env mirror landed). Nothing else records
  that, which is why it survives phase-62 rather than being replaced.
- ``GET /projects/{id}/deploy/latest/logs/{target}`` — the **provider's** log for one target
  (phase-62). What "Logs" on a Vercel node ought to have meant all along.
- ``GET /projects/{id}/deploy/config`` — which backend provider is configured, so the UI can name
  the right credential in BYO mode instead of assuming Vercel.

Deploying is *not* here: it runs through the conductor (``POST /projects/{id}/intent`` with
``stage=deploy``), so stage legality — the deploy⇐build hard prereq (§8) — stays enforced in exactly
one place. **Deleting** is here (phase-62): it is not a stage transition the conductor could route,
and it owns resetting the stage itself.

Nothing in these payloads is sensitive: a ``Deployment`` records URLs and topology, never env values
(§7), and a provider log is build output, not configuration.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.auth.deps import get_current_user_id
from app.core.errors import NotFoundError
from app.db.blobs import get_blob_store
from app.db.models import STATUS_DELETED, Deployment
from app.db.models.enums import CredentialKind, DeployMode
from app.deploy.providers import DeployTarget, provider_for
from app.deploy.providers.base import DeployRef
from app.deploy.teardown import DeploymentTeardown
from app.projects.service import ProjectService, parse_object_id

deploy_router = APIRouter(prefix="/projects", tags=["deploy"])


class DeploymentPublic(BaseModel):
    """A deployment as the topology UI sees it — URLs and shape, no env values."""

    id: str
    mode: DeployMode
    status: str
    fe_target: str | None = None
    be_target: str | None = None
    db_target: str | None = None
    urls: dict[str, str] = Field(default_factory=dict)
    topology_snapshot: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class DeployLogsPublic(BaseModel):
    """``log`` is empty when the deploy stored none (or the blob has since gone)."""

    deployment_id: str
    log: str


class ProviderLogLine(BaseModel):
    message: str
    ts: str | None = None


class ProviderLogsPublic(BaseModel):
    """One target's log **as the provider tells it**, or why it could not be fetched.

    ``error`` rather than a failed request: a revoked token, a deployment the user deleted in the
    provider's dashboard, or a record written before deployment refs were persisted are all normal
    states of the world. None of them should break the panel, so each comes back as an empty list
    plus a sentence the drawer can show.
    """

    deployment_id: str
    target: str
    provider: str | None = None
    lines: list[ProviderLogLine] = Field(default_factory=list)
    error: str | None = None


class DeployConfigPublic(BaseModel):
    """Deploy-shape facts the UI needs but cannot infer. No secrets, no per-user state."""

    #: Provider key serving the backend target — ``vercel`` by default, ``render`` when configured.
    be_provider: str
    #: Credential kinds a BYO deploy requires before it can start.
    byo_required_credentials: list[str] = Field(default_factory=list)
    #: Credential kinds a BYO deploy can use but does not need (the platform DB covers this one).
    byo_optional_credentials: list[str] = Field(default_factory=list)


class DeployDeleteResponse(BaseModel):
    """Outcome of a teardown. ``warnings`` name provider resources that outlived it."""

    deployment_id: str
    destroyed: list[str]
    record_deleted: bool
    warnings: list[str]


async def _latest(project_id: str, user_id: PydanticObjectId) -> Deployment | None:
    pid = parse_object_id(project_id)
    await ProjectService().get_owned(pid, user_id)
    # Mongo stores datetimes at millisecond precision, so two deploys can tie on ``created_at``;
    # ``_id`` is monotonic and breaks the tie in real insertion order.
    return await Deployment.find({"project_id": pid}).sort("-created_at", "-_id").first_or_none()


def _to_public(deployment: Deployment) -> DeploymentPublic:
    return DeploymentPublic(
        id=str(deployment.id),
        mode=deployment.mode,
        status=deployment.status,
        fe_target=deployment.fe_target,
        be_target=deployment.be_target,
        db_target=deployment.db_target,
        urls=deployment.urls,
        topology_snapshot=deployment.topology_snapshot,
        created_at=deployment.created_at,
    )


@deploy_router.get("/{project_id}/deploy/latest", response_model=DeploymentPublic | None)
async def latest_deployment(
    project_id: str, user_id: PydanticObjectId = Depends(get_current_user_id)
) -> DeploymentPublic | None:
    """The most recent deployment, or ``null`` when the project has never been deployed."""
    deployment = await _latest(project_id, user_id)
    return None if deployment is None else _to_public(deployment)


@deploy_router.get("/{project_id}/deploy/latest/logs", response_model=DeployLogsPublic | None)
async def latest_deployment_logs(
    project_id: str, user_id: PydanticObjectId = Depends(get_current_user_id)
) -> DeployLogsPublic | None:
    """The step log the last deploy recorded (db/be/fe lines), for the per-node log view."""
    deployment = await _latest(project_id, user_id)
    if deployment is None:
        return None

    log = ""
    if deployment.logs_ref:
        try:
            log = (await get_blob_store().get(deployment.logs_ref)).decode("utf-8")
        except Exception:  # a missing blob is an empty log, not a broken panel
            log = ""
    return DeployLogsPublic(deployment_id=str(deployment.id), log=log)


@deploy_router.get(
    "/{project_id}/deploy/latest/logs/{target}", response_model=ProviderLogsPublic | None
)
async def latest_deployment_provider_logs(
    project_id: str,
    target: str,
    user_id: PydanticObjectId = Depends(get_current_user_id),
) -> ProviderLogsPublic | None:
    """The **provider's** own log for one deployed target — Vercel's build output, not ours.

    Total by design: every topology node id is answerable, and no provider problem is an error
    status. A node with no provider deployment (``db``), a record predating ref persistence, a
    revoked token, or a deployment removed in the provider's own dashboard all come back as an empty
    list plus ``error`` — because a log fetch must never be able to break the topology panel.
    ``target`` is therefore a plain string, not the ``fe``/``be`` enum: ``db`` is a node the UI can
    legitimately ask about and deserves a sentence, not a 422.
    """
    deployment = await _latest(project_id, user_id)
    if deployment is None:
        return None

    base = ProviderLogsPublic(deployment_id=str(deployment.id), target=target)
    if target not in tuple(DeployTarget):
        base.error = (
            "The database is not a provider deployment — it has no build log."
            if target == "db"
            else f"{target} is not a deployable target."
        )
        return base
    resolved = DeployTarget(target)

    ref = deployment.refs.get(str(resolved))
    if ref is None:
        base.error = (
            "This deployment predates provider log support, so its deployment id was never "
            "recorded. Redeploy to get provider logs; the pipeline log still shows what ForgeFlow "
            "did."
        )
        return base

    base.provider = ref.provider
    try:
        lines = await provider_for(resolved).logs(
            DeployRef(id=ref.id, project=ref.project, target=resolved),
            mode=deployment.mode,
            user_id=user_id,
        )
    except Exception as exc:  # noqa: BLE001 - a log fetch must never break the panel
        base.error = str(exc)
        return base

    base.lines = [ProviderLogLine(message=line.message, ts=line.ts) for line in lines]
    return base


@deploy_router.get("/{project_id}/deploy/config", response_model=DeployConfigPublic)
async def deploy_config(
    project_id: str, user_id: PydanticObjectId = Depends(get_current_user_id)
) -> DeployConfigPublic:
    """Which credentials a BYO deploy on this instance needs, so the UI can check before starting.

    The backend provider is configurable (``DEPLOY_BE_PROVIDER``), so the UI cannot assume Vercel —
    asking a Render-backed instance for a Vercel token would be wrong in exactly the case the
    readiness check exists to prevent.
    """
    await ProjectService().get_owned(parse_object_id(project_id), user_id)
    fe_kind = provider_for(DeployTarget.fe).credential_kind
    be_provider = provider_for(DeployTarget.be)
    required = sorted({str(fe_kind), str(be_provider.credential_kind)})
    return DeployConfigPublic(
        be_provider=be_provider.key,
        byo_required_credentials=required,
        # The platform database covers this one; a BYO Mongo URI is an override, not a prerequisite.
        byo_optional_credentials=[str(CredentialKind.mongo_uri)],
    )


@deploy_router.delete("/{project_id}/deploy/latest", response_model=DeployDeleteResponse)
async def delete_latest_deployment(
    project_id: str, user_id: PydanticObjectId = Depends(get_current_user_id)
) -> DeployDeleteResponse:
    """Take the current deployment down: destroy it at the provider and retire the record.

    Hosting only — the project's database and its data are untouched. Returns a report rather than a
    bare ``204`` for the same reason project delete does: provider teardown fails soft, and a caller
    that cannot see what was left behind has no way to tell the user.
    """
    deployment = await _latest(project_id, user_id)
    if deployment is None:
        raise NotFoundError("This project has no deployment to delete")
    if deployment.status == STATUS_DELETED:
        raise NotFoundError("This deployment has already been deleted")

    report = await DeploymentTeardown().destroy(deployment, user_id=user_id)
    return DeployDeleteResponse(
        deployment_id=str(deployment.id),
        destroyed=report.destroyed,
        record_deleted=report.record_deleted,
        warnings=report.warnings,
    )
