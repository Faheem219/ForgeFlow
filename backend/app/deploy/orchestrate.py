"""Deploy orchestration (phase-37, D11) — one action takes a passing build to a healthy live app.

The sequence is fixed by the wiring dependencies (§8, risk §12):

    prereq (build complete) → infra plan → provision DB → deploy BE (Render, +MONGODB_URI) →
    build FE with VITE_API_BASE_URL=<BE url> → deploy FE (Vercel) → health-check → record Deployment

**Backend before frontend** is not a preference: the SPA bakes ``VITE_API_BASE_URL`` at *build*
time, so the backend must be live and its URL known before the frontend is built and shipped. Each
step emits a ``deploy.status`` event (the topology UI, phase-38, renders the ``topology_snapshot``
recorded here). Partial failures degrade rather than crash — a live BE with a failed FE is reported
as ``degraded`` with the BE URL intact, and a re-deploy is always safe (a fresh ``Deployment``).

Every collaborator (analyzer, DB provisioner, provider adapters, the FE builder) is injected behind
a seam, so the whole ordering/wiring/failure logic is exercised without Docker or a real provider.
"""

from __future__ import annotations

import logging
import shlex
import time
from collections.abc import Awaitable, Callable
from typing import Any

from beanie import PydanticObjectId

from app.core.config import get_config
from app.core.errors import ProviderError, UserError
from app.db.blobs import BlobStore, get_blob_store
from app.db.models import Deployment, Project
from app.db.models.enums import DeployMode, Stage, StageStatus
from app.db.repos import StageStateRepo
from app.deploy.analyzer import BackendPlan, FrontendPlan, InfraAnalyzer, InfraPlan
from app.deploy.db_provision import DbProvisioner
from app.deploy.providers import DeployProvider, DeployTarget, provider_for
from app.deploy.providers.base import DeployResult, DeploySpec, DeployState
from app.realtime.hub import emit
from app.realtime.schemas import EventType

logger = logging.getLogger(__name__)

# Deployment.status values.
STATUS_LIVE = "live"
STATUS_DEGRADED = "degraded"
STATUS_FAILED = "failed"

_POLL_INTERVAL_S = 3.0
_TERMINAL = frozenset({DeployState.live, DeployState.failed, DeployState.canceled})

# A workspace-relative built-SPA file set (path → content), and the builder that produces it.
FrontendBuilder = Callable[[Project, FrontendPlan, dict[str, str]], Awaitable[dict[str, str]]]

#: The analyzer slice the orchestrator needs (tests inject a plan directly).
PlanSource = Callable[[Project], Awaitable[InfraPlan]]


class DeployOrchestrator:
    def __init__(
        self,
        *,
        plan_source: PlanSource | None = None,
        frontend_builder: FrontendBuilder | None = None,
        db: DbProvisioner | None = None,
        provider_factory: Callable[[DeployTarget], DeployProvider] = provider_for,
        blob_store: BlobStore | None = None,
        emitter: Callable[..., Awaitable[Any]] = emit,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._plan_source = plan_source or _analyze
        self._build_frontend = frontend_builder or _WorkspaceFrontendBuilder()
        self._db = db or DbProvisioner()
        self._provider_factory = provider_factory
        self._blobs = blob_store
        self._emit = emitter
        self._stages = StageStateRepo()
        import asyncio

        self._sleep = sleep or asyncio.sleep

    async def deploy(
        self, project: Project, *, mode: DeployMode, user_id: PydanticObjectId | None = None
    ) -> Deployment:
        project_id = project.id
        if project_id is None:  # pragma: no cover - a persisted project always carries an id
            raise UserError("Project is not persisted")
        channel = str(project_id)

        await self._require_build(project_id)
        plan = await self._plan_source(project)
        log: list[str] = []

        # 1) Database — provision/resolve the isolated URI the backend connects with.
        mongodb_uri: str | None = None
        db_target: str | None = None
        if plan.db is not None:
            await self._step(channel, "db", "provisioning")
            info = await self._db.provision(project)
            mongodb_uri = await self._db.get_app_mongodb_uri(project)
            db_target = str(info.mode)
            log.append(f"db: provisioned ({db_target})")
            await self._step(channel, "db", "ready", target=db_target)

        # 2) Backend → Render (before the frontend, which needs its URL).
        be_result: DeployResult | None = None
        be_error: ProviderError | None = None
        be_url: str | None = None
        if plan.be is not None:
            await self._step(channel, "be", "deploying")
            try:
                be_result = await self._deploy_backend(
                    project, plan.be, mongodb_uri, mode=mode, user_id=user_id
                )
                be_url = be_result.url
                log.append(f"be: {be_result.status} {be_url or ''}".strip())
                await self._step(channel, "be", str(be_result.status), url=be_url)
            except ProviderError as exc:
                be_error = exc
                log.append(f"be: FAILED — {exc}")
                await self._step(channel, "be", "failed", error=str(exc))

        # 3) Frontend → Vercel (built with the BE URL, then shipped). Skipped if the BE failed.
        fe_result: DeployResult | None = None
        fe_error: ProviderError | None = None
        if plan.fe is not None and be_error is None:
            await self._step(channel, "fe", "building")
            try:
                fe_result = await self._deploy_frontend(
                    project, plan.fe, be_url, mode=mode, user_id=user_id
                )
                log.append(f"fe: {fe_result.status} {fe_result.url or ''}".strip())
                await self._step(channel, "fe", str(fe_result.status), url=fe_result.url)
            except ProviderError as exc:
                fe_error = exc
                log.append(f"fe: FAILED — {exc}")
                await self._step(channel, "fe", "failed", error=str(exc))

        # 4) Health + record.
        status = _overall_status(plan, be_result, be_error, fe_result, fe_error)
        urls = _urls(be_result, fe_result)
        deployment = await Deployment(
            project_id=project_id,
            mode=mode,
            fe_target="vercel" if plan.fe is not None else None,
            be_target="render" if plan.be is not None else None,
            db_target=db_target,
            urls=urls,
            status=status,
            topology_snapshot=_topology(plan, be_result, fe_result, db_target, status),
            logs_ref=await self._store_log(log),
        ).insert()

        await self._step(
            channel,
            "health",
            status,
            deployment_id=str(deployment.id),
            fe_url=urls.get("fe"),
            be_url=urls.get("be"),
        )
        return deployment

    # -- steps ------------------------------------------------------------------------------

    async def _require_build(self, project_id: PydanticObjectId) -> None:
        state = await self._stages.get_or_create(project_id, Stage.build)
        if state.status is not StageStatus.complete:
            raise UserError(
                "Deploy needs a passing build first — run the Build stage until it boots healthy."
            )

    async def _deploy_backend(
        self,
        project: Project,
        be: BackendPlan,
        mongodb_uri: str | None,
        *,
        mode: DeployMode,
        user_id: PydanticObjectId | None,
    ) -> DeployResult:
        provider = self._provider_factory(DeployTarget.be)
        env = {"NODE_ENV": "production"}
        if mongodb_uri:
            env["MONGODB_URI"] = mongodb_uri
        config = get_config()
        spec = DeploySpec(
            name=_service_name(project, "api"),
            target=DeployTarget.be,
            env=env,
            repo_url=str(config.get("deploy_repo_url")) or None,
            branch="main",
            root_dir=be.dir,
            build_cmd=be.build_cmd,
            start_cmd=be.start_cmd,
            port=be.port,
            region=str(config.get("deploy_region")) or None,
        )
        result = await provider.deploy(spec, mode=mode, user_id=user_id)
        return await self._await_live(provider, result, mode=mode, user_id=user_id)

    async def _deploy_frontend(
        self,
        project: Project,
        fe: FrontendPlan,
        be_url: str | None,
        *,
        mode: DeployMode,
        user_id: PydanticObjectId | None,
    ) -> DeployResult:
        # VITE_API_BASE_URL is a build-time var → it must be present when the SPA is compiled.
        build_env = {"NODE_ENV": "production"}
        if be_url:
            build_env["VITE_API_BASE_URL"] = be_url
        files = await self._build_frontend(project, fe, build_env)

        provider = self._provider_factory(DeployTarget.fe)
        env = {"VITE_API_BASE_URL": be_url} if be_url else {}
        spec = DeploySpec(
            name=_service_name(project, "web"), target=DeployTarget.fe, env=env, files=files
        )
        result = await provider.deploy(spec, mode=mode, user_id=user_id)
        return await self._await_live(provider, result, mode=mode, user_id=user_id)

    async def _await_live(
        self,
        provider: DeployProvider,
        result: DeployResult,
        *,
        mode: DeployMode,
        user_id: PydanticObjectId | None,
    ) -> DeployResult:
        """Poll until the deploy reaches a terminal state (live/failed/canceled) or times out."""
        deadline = time.monotonic() + float(get_config().get("deploy_health_timeout_s"))
        while result.status not in _TERMINAL and time.monotonic() < deadline:
            await self._sleep(_POLL_INTERVAL_S)
            result = await provider.status(result.ref, mode=mode, user_id=user_id)
        return result

    async def _step(self, channel: str, step: str, status: str, **payload: Any) -> None:
        await self._emit(
            channel,
            EventType.deploy_status,
            {"step": step, "status": status, **payload},
            stage=Stage.deploy,
        )

    async def _store_log(self, lines: list[str]) -> str | None:
        if not lines:
            return None
        store = self._blobs if self._blobs is not None else get_blob_store()
        try:
            return await store.put("\n".join(lines).encode("utf-8"))
        except Exception:  # blob trouble must not fail an otherwise-good deploy
            logger.warning("failed to store deploy log", exc_info=True)
            return None


# --------------------------------------------------------------------- pure helpers


def _service_name(project: Project, suffix: str) -> str:
    """A stable, provider-valid (lowercase, dashed) service name for a project's target."""
    return f"forgeflow-{suffix}-{str(project.id)[-8:]}"


def _urls(be: DeployResult | None, fe: DeployResult | None) -> dict[str, str]:
    urls: dict[str, str] = {}
    if be is not None and be.url:
        urls["be"] = be.url
    if fe is not None and fe.url:
        urls["fe"] = fe.url
    return urls


def _is_live(result: DeployResult | None, error: ProviderError | None) -> bool:
    return error is None and result is not None and result.status is DeployState.live


def _overall_status(
    plan: InfraPlan,
    be: DeployResult | None,
    be_error: ProviderError | None,
    fe: DeployResult | None,
    fe_error: ProviderError | None,
) -> str:
    # A backend that never came up leaves nothing usable → failed.
    if plan.be is not None and not _is_live(be, be_error):
        return STATUS_FAILED
    # Any expected target that isn't live (e.g. FE failed after BE went live) → degraded.
    if plan.fe is not None and not _is_live(fe, fe_error):
        return STATUS_DEGRADED
    return STATUS_LIVE


def _topology(
    plan: InfraPlan,
    be: DeployResult | None,
    fe: DeployResult | None,
    db_target: str | None,
    status: str,
) -> dict[str, Any]:
    """The graph phase-38 renders — real nodes/edges/urls from this deploy, not a mock."""
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    if plan.fe is not None:
        nodes.append(
            {
                "id": "fe",
                "kind": "frontend",
                "provider": "vercel",
                "url": fe.url if fe else None,
                "status": str(fe.status) if fe else "failed",
            }
        )
    if plan.be is not None:
        nodes.append(
            {
                "id": "be",
                "kind": "backend",
                "provider": "render",
                "url": be.url if be else None,
                "status": str(be.status) if be else "failed",
            }
        )
    if plan.db is not None:
        nodes.append(
            {"id": "db", "kind": "database", "provider": db_target or "platform", "status": "ready"}
        )
    if plan.fe is not None and plan.be is not None:
        edges.append({"source": "fe", "target": "be", "label": "VITE_API_BASE_URL"})
    if plan.be is not None and plan.db is not None:
        edges.append({"source": "be", "target": "db", "label": "MONGODB_URI"})
    return {"nodes": nodes, "edges": edges, "status": status}


# --------------------------------------------------------------------- default collaborators


async def _analyze(project: Project) -> InfraPlan:
    """Default plan source: classify the workspace without persisting a new plan version."""
    return await InfraAnalyzer().analyze(project, persist=False)


class _WorkspaceFrontendBuilder:
    """Production FE builder: run the plan's build in the sandbox, read the output dir back."""

    async def __call__(  # pragma: no cover - needs the Docker sandbox toolchain
        self, project: Project, fe: FrontendPlan, build_env: dict[str, str]
    ) -> dict[str, str]:
        from app.sandbox.exec import get_exec_service
        from app.sandbox.workspace import WorkspaceService

        workspace = WorkspaceService()
        exec_service = get_exec_service()
        for cmd in (fe.install_cmd, fe.build_cmd):
            await exec_service.run(project, shlex.split(cmd), cwd=fe.dir, env=build_env)

        out_dir = f"{fe.dir}/{fe.output_dir}".strip("/")
        prefix = out_dir + "/"
        files: dict[str, str] = {}
        for node in await workspace.tree(project, out_dir, depth=None):
            if node.type != "file":
                continue
            content = await workspace.read(project, node.path)
            rel = node.path[len(prefix) :] if node.path.startswith(prefix) else node.path
            files[rel] = content.content
        if not files:
            raise UserError("Frontend build produced no output to deploy")
        return files


__all__ = [
    "DeployOrchestrator",
    "FrontendBuilder",
    "STATUS_DEGRADED",
    "STATUS_FAILED",
    "STATUS_LIVE",
]
