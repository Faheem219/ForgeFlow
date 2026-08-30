"""Deploy orchestration (phase-37, D11) — one action takes a passing build to a healthy live app.

The sequence is fixed by the wiring dependencies (§8, risk §12):

    prereq (build complete) → infra plan → provision DB → deploy BE (+MONGODB_URI) →
    build FE with VITE_API_BASE_URL=<BE url> → deploy FE (Vercel) → health-check → record Deployment

Since phase-58 the backend ships to **Vercel** by default, as an inline upload of the workspace's
backend directory (``DEPLOY_BE_PROVIDER=render`` restores the repo-clone path).

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
from app.db.models import Deployment, DeploymentRef, Project
from app.db.models.enums import DeployMode, Stage, StageStatus
from app.db.repos import StageStateRepo
from app.deploy.analyzer import BackendPlan, FrontendPlan, InfraAnalyzer, InfraPlan
from app.deploy.db_provision import DbProvisioner
from app.deploy.providers import (
    DeployProvider,
    DeployTarget,
    backend_uploads_source,
    provider_for,
)
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

#: Reads the backend source out of the workspace (path → content) for an inline upload (phase-58).
#: A seam like the FE builder, so the ordering/wiring logic is testable without Docker.
BackendSource = Callable[[Project, BackendPlan], Awaitable[dict[str, str]]]

#: The analyzer slice the orchestrator needs (tests inject a plan directly).
PlanSource = Callable[[Project], Awaitable[InfraPlan]]


class DeployOrchestrator:
    def __init__(
        self,
        *,
        plan_source: PlanSource | None = None,
        frontend_builder: FrontendBuilder | None = None,
        backend_source: BackendSource | None = None,
        db: DbProvisioner | None = None,
        provider_factory: Callable[[DeployTarget], DeployProvider] = provider_for,
        blob_store: BlobStore | None = None,
        emitter: Callable[..., Awaitable[Any]] = emit,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._plan_source = plan_source or _analyze
        self._build_frontend = frontend_builder or _WorkspaceFrontendBuilder()
        self._backend_source = backend_source or _WorkspaceBackendSource()
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
            # Before ANY provider call: a database the deployed app cannot reach must cost an error
            # message, not two billed deployments that come up and then time out on every query.
            await self._db.assert_deployable(project)
            info = await self._db.provision(project)
            mongodb_uri = await self._db.get_app_mongodb_uri(project)
            db_target = str(info.mode)
            log.append(f"db: provisioned ({db_target})")
            await self._step(channel, "db", "ready", target=db_target)

        # 2) Backend (before the frontend, which needs its URL).
        be_result: DeployResult | None = None
        be_error: Exception | None = None
        be_url: str | None = None
        if plan.be is not None:
            await self._step(channel, "be", "deploying")
            try:
                be_result = await self._deploy_backend(
                    project, plan.be, mongodb_uri, mode=mode, user_id=user_id, log=log
                )
                be_url = be_result.url
                log.append(f"be: {be_result.status} {be_url or ''}".strip())
                await self._step(channel, "be", str(be_result.status), url=be_url)
            except (ProviderError, UserError) as exc:
                # UserError too: the source reader rejects an empty backend dir / a missing
                # vercel.json, and letting that escape would abandon the deploy *after* the DB was
                # provisioned, with no Deployment written to show for it.
                be_error = exc
                log.append(f"be: FAILED — {exc}")
                await self._step(channel, "be", "failed", error=str(exc))

        # 3) Frontend → Vercel (built with the BE URL, then shipped). Skipped unless the backend is
        # actually LIVE — not merely "did not raise". A provider that returns `failed`, or that is
        # still building when _await_live times out, raises nothing; shipping a real (billed)
        # frontend wired to that backend produces a live SPA calling an API that is not there.
        # This is the same definition _overall_status uses, so the two can no longer disagree.
        fe_result: DeployResult | None = None
        fe_error: Exception | None = None
        backend_ok = plan.be is None or _is_live(be_result, be_error)
        if plan.fe is None:
            # A backend-only project is legal, so this is not an error — but it is never something
            # the user should have to infer from a frontend node quietly missing off the graph.
            # Say it, in the log and on the wire, whatever the reason the analyzer found none.
            reason = "no frontend was detected in the workspace, so none was deployed"
            log.append(f"fe: skipped — {reason}")
            await self._step(channel, "fe", "skipped", error=reason)
        if plan.fe is not None and not backend_ok:
            reason = "the backend did not come up, so there is nothing to point the frontend at"
            log.append(f"fe: skipped — {reason}")
            await self._step(channel, "fe", "skipped", error=reason)
        if plan.fe is not None and backend_ok:
            await self._step(channel, "fe", "building")
            try:
                fe_result = await self._deploy_frontend(
                    project, plan.fe, be_url, mode=mode, user_id=user_id, log=log
                )
                log.append(f"fe: {fe_result.status} {fe_result.url or ''}".strip())
                await self._step(channel, "fe", str(fe_result.status), url=fe_result.url)
            except (ProviderError, UserError) as exc:
                # UserError is how a failed *build* arrives (the SPA is compiled here, not at the
                # provider). It degrades the deploy exactly like a provider failure would: the
                # backend is already live and billed, so the Deployment must still be recorded.
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
            be_target=(
                (be_result.provider if be_result else _configured_be_provider())
                if plan.be is not None
                else None
            ),
            db_target=db_target,
            urls=urls,
            refs=_refs(be_result, fe_result),
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
        log: list[str],
    ) -> DeployResult:
        provider = self._provider_factory(DeployTarget.be)
        env = {"NODE_ENV": "production"}
        if mongodb_uri:
            env["MONGODB_URI"] = mongodb_uri
        config = get_config()
        # Only collect the workspace when the provider will actually upload it — reading the source
        # costs sandbox round-trips, and the repo-clone path has no use for it.
        files = await self._backend_source(project, be) if backend_uploads_source() else None
        spec = DeploySpec(
            name=_service_name(project, "api"),
            target=DeployTarget.be,
            env=env,
            files=files,
            repo_url=str(config.get("deploy_repo_url")) or None,
            branch="main",
            root_dir=be.dir,
            build_cmd=be.build_cmd,
            start_cmd=be.start_cmd,
            port=be.port,
            region=str(config.get("deploy_region")) or None,
        )
        result = await provider.deploy(spec, mode=mode, user_id=user_id)
        result = await self._await_live(provider, result, mode=mode, user_id=user_id)
        await self._mirror_env(provider, result, env, mode=mode, user_id=user_id, log=log)
        return result

    async def _deploy_frontend(
        self,
        project: Project,
        fe: FrontendPlan,
        be_url: str | None,
        *,
        mode: DeployMode,
        user_id: PydanticObjectId | None,
        log: list[str],
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
        result = await self._await_live(provider, result, mode=mode, user_id=user_id)
        await self._mirror_env(provider, result, env, mode=mode, user_id=user_id, log=log)
        return result

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

    async def _mirror_env(
        self,
        provider: DeployProvider,
        result: DeployResult,
        env: dict[str, str],
        *,
        mode: DeployMode,
        user_id: PydanticObjectId | None,
        log: list[str],
    ) -> None:
        """Copy this deploy's env onto the provider **project**, not just the deployment.

        The inline env in the creation body is what makes *this* deployment correct, and it stays.
        But Vercel scopes it to that immutable deployment: it never shows in the project's
        environment-variable settings, and a redeploy triggered from the provider's own dashboard
        inherits *project* env — of which there was none, so the backend came up with no
        ``MONGODB_URI``. The project-scoped copy is what makes the dashboard honest and every later
        deployment work. They answer different questions; the cost of both is one idempotent upsert.

        **Fail soft.** The deployment is already live and correct by the time this runs, so a
        rejected env write is recorded and moved past — never allowed to turn a good deploy into a
        failed one.
        """
        if not env:
            return
        try:
            await provider.set_env(result.ref, env, mode=mode, user_id=user_id)
            log.append(f"env: {result.target} project vars set ({', '.join(sorted(env))})")
        except Exception as exc:  # noqa: BLE001 - cosmetic follow-up; never fails the deploy
            log.append(f"env: {result.target} project vars NOT set — {exc}")
            logger.warning(
                "could not mirror env to the provider project",
                extra={"target": str(result.target), "provider": result.provider},
                exc_info=True,
            )

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


def _refs(be: DeployResult | None, fe: DeployResult | None) -> dict[str, DeploymentRef]:
    """Provider handles per target — what logs and teardown key off after the deploy returns."""
    refs: dict[str, DeploymentRef] = {}
    for result in (be, fe):
        if result is None:
            continue
        refs[str(result.target)] = DeploymentRef(
            provider=result.provider, id=result.ref.id, project=result.ref.project
        )
    return refs


def _is_live(result: DeployResult | None, error: Exception | None) -> bool:
    return error is None and result is not None and result.status is DeployState.live


def _overall_status(
    plan: InfraPlan,
    be: DeployResult | None,
    be_error: Exception | None,
    fe: DeployResult | None,
    fe_error: Exception | None,
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
                # The vendor is configurable since phase-58, so read it off the result rather than
                # hardcoding it; a failed deploy has no result, so fall back to what is configured.
                "provider": be.provider if be else _configured_be_provider(),
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
    """Default plan source: classify the workspace without persisting a new plan version.

    The workspace is opened **before** the analysis rather than lazily by its first file read. A
    sandbox idle-reaped overnight is started by whichever call touches it first, and that call is
    the frontend's ``package.json`` — so a cold-start hiccup used to surface as "this project has
    no frontend", and the deploy then shipped a backend-only app and called it live.
    """
    from app.sandbox.workspace import WorkspaceService

    await WorkspaceService().ensure_workspace(project)
    return await InfraAnalyzer().analyze(project, persist=False)


#: How much of a failed build's output rides along in the error the user sees. Enough to carry a
#: compiler error and its frame, short enough to stay readable in the stage conversation.
_BUILD_LOG_TAIL_CHARS = 2000


async def _exec_output(outcome: Any) -> str:
    """A finished command's captured output, or ``""`` when none was (or could be) stored."""
    ref = getattr(outcome, "output_ref", None)
    if not ref:
        return ""
    try:
        return (await get_blob_store().get(ref)).decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - a missing log must not mask the failure it describes
        logger.warning("could not read build output for a failed deploy step", exc_info=True)
        return ""


def _build_step_failed(cmd: str, outcome: Any, output: str) -> str:
    """The user-facing message for a build command that did not succeed — with its output."""
    detail = "timed out" if outcome.timed_out else f"exited {outcome.exit_code}"
    message = f"The frontend build failed: `{cmd}` {detail}."
    tail = output.strip()[-_BUILD_LOG_TAIL_CHARS:].strip()
    return f"{message}\n\n{tail}" if tail else message


class _WorkspaceFrontendBuilder:
    """Production FE builder: run the plan's build in the sandbox, read the output dir back.

    Both collaborators are injectable for the same reason every other one in this module is: the
    ordering and the failure handling are the parts that go wrong, and they should be provable
    without a Docker daemon or a Node toolchain.
    """

    def __init__(self, workspace: Any | None = None, exec_service: Any | None = None) -> None:
        self._workspace = workspace
        self._exec = exec_service

    async def __call__(
        self, project: Project, fe: FrontendPlan, build_env: dict[str, str]
    ) -> dict[str, str]:
        from app.sandbox.exec import get_exec_service
        from app.sandbox.workspace import WorkspaceService

        workspace = self._workspace or WorkspaceService()
        exec_service = self._exec or get_exec_service()
        # The install must NOT see NODE_ENV=production. pnpm reads that as `--prod` and sets out to
        # remove devDependencies — where vite and typescript live, i.e. everything the build is
        # about to need. It announces "the modules directories will be removed and reinstalled from
        # scratch", and a sandbox exec has no stdin, so it answers its own prompt with EOF and exits
        # **0** having installed nothing. The build still gets it: that is the flag vite bakes in.
        install_env = {k: v for k, v in build_env.items() if k != "NODE_ENV"}
        for cmd, env in ((fe.install_cmd, install_env), (fe.build_cmd, build_env)):
            # The exit code is the only thing separating "the SPA compiled" from "vite threw". Drop
            # it and a failed build arrives at the upload step as an empty output directory, to be
            # reported as "produced no output" — which describes the symptom and hides the cause.
            outcome = await exec_service.run(project, shlex.split(cmd), cwd=fe.dir, env=env)
            if outcome.timed_out or outcome.exit_code != 0:
                raise UserError(_build_step_failed(cmd, outcome, await _exec_output(outcome)))

        out_dir = f"{fe.dir}/{fe.output_dir}".strip("/")
        prefix = out_dir + "/"
        files: dict[str, str] = {}
        for node in await workspace.tree(project, out_dir, depth=None):
            if node.type != "file":
                continue
            try:
                content = await workspace.read(project, node.path)
            except UserError as exc:
                # A bundle ships as text (``DeploySpec.files``), so an image or font in the build
                # output cannot go up. Name the file: the generic message sent the last user
                # looking for a build error that was never there.
                raise UserError(
                    f"'{node.path}' cannot be uploaded with the frontend bundle — {exc}. "
                    "Binary assets in the build output are not supported yet."
                ) from exc
            rel = node.path[len(prefix) :] if node.path.startswith(prefix) else node.path
            files[rel] = content.content
        if not files:
            raise UserError(
                f"`{fe.build_cmd}` succeeded but wrote nothing to '{out_dir}' — check that the "
                "build actually outputs there."
            )
        return files


#: Never uploaded with the backend source: install output, git history, build artifacts and
#: ForgeFlow's own in-workspace bookkeeping. node_modules alone would be tens of thousands of files.
_BE_SOURCE_SKIP_DIRS = frozenset({"node_modules", ".git", "dist", "build", ".turbo", ".forgeflow"})


class _WorkspaceBackendSource:
    """Read the backend directory out of the sandbox for an inline upload (phase-58).

    The workspace stays the single source of truth: what ships is the code the build stage wrote and
    the repair loop validated, read through the archive API. Untrusted code is never executed here —
    it is copied.
    """

    async def __call__(  # pragma: no cover - needs the Docker sandbox
        self, project: Project, be: BackendPlan
    ) -> dict[str, str]:
        from app.sandbox.workspace import WorkspaceService

        workspace = WorkspaceService()
        root = be.dir.strip("/")
        prefix = f"{root}/" if root else ""
        files: dict[str, str] = {}
        for node in await workspace.tree(project, root, depth=None):
            if node.type != "file":
                continue
            rel = node.path[len(prefix) :] if node.path.startswith(prefix) else node.path
            if any(part in _BE_SOURCE_SKIP_DIRS for part in rel.split("/")):
                continue
            content = await workspace.read(project, node.path)
            files[rel] = content.content
        if not files:
            raise UserError("The backend directory is empty — nothing to deploy")
        if "vercel.json" not in files:
            raise UserError(
                "The backend has no vercel.json — it cannot be deployed as a Vercel function. "
                "Re-run Build so the skeleton's deploy entrypoint is present."
            )
        return files


def _configured_be_provider() -> str:
    """The backend provider name recorded on a Deployment when no result carries one."""
    return "vercel" if backend_uploads_source() else "render"


__all__ = [
    "DeployOrchestrator",
    "BackendSource",
    "FrontendBuilder",
    "STATUS_DEGRADED",
    "STATUS_FAILED",
    "STATUS_LIVE",
]
