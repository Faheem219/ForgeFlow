"""Live preview: run the generated app's dev servers inside its sandbox (phase-15).

Starts the skeleton's FE (Vite) and BE (Express) via phase-13 exec, streams their logs as
``terminal.output`` tagged per process, health-polls both, and resolves the proxied URLs.

Two things worth knowing:

- **Health probes run inside the sandbox** (``curl`` against loopback), not from the control
  plane. That keeps the check working regardless of how the sandbox is (or isn't) networked, and
  it needs no host access.
- **Networking** is a deliberate relaxation of phase-11's sealed per-project network: on start the
  sandbox *also* joins a dedicated *internal* docker network (see ``build_preview_network_kwargs``)
  so the reverse proxy can reach the dev servers. Still no host network and no internet.
- **Dependencies** are ensured before the servers launch (:mod:`app.sandbox.deps`) — the skeleton is
  copied in as source only, so without that step there is nothing to run.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shlex
import time
from collections import deque
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from app.core.config import get_config

# SystemError shadows the builtin (taxonomy name fixed by the plan); A004 suppressed on-line.
from app.core.errors import SystemError, UserError  # noqa: A004
from app.db.models import Project
from app.realtime.hub import emit
from app.realtime.schemas import EventType
from app.sandbox.deps import ensure_dependencies
from app.sandbox.exec import aiter_chunks
from app.sandbox.manager import container_name, get_manager
from app.sandbox.ports import (
    PROCESS_COUNT_SCRIPT,
    parse_busy_ports,
    parse_process_count,
    reclaim_script,
    wrap_with_pidfile,
)
from app.sandbox.runtime import DockerRuntime, ExecHandle, WorkspaceRuntime
from app.sandbox.schemas import PreviewInfo, PreviewProcess, PreviewStatus
from app.sandbox.workspace import RuntimeProvider, active_runtime_provider

logger = logging.getLogger(__name__)

_PROBE_TIMEOUT_S = 2


def _new_log_tail() -> deque[str]:
    """A bounded ring of the process's most recent output lines — boot-failure evidence."""
    return deque(maxlen=int(get_config().get("preview_log_tail_lines")))


@dataclass
class _Proc:
    handle: ExecHandle
    port: int
    health_path: str
    task: asyncio.Task[None] | None = None
    exited: bool = False
    exit_code: int | None = None
    # The recent stdout/stderr, so a crashed dev server leaves something to diagnose from (ph-55).
    log_tail: deque[str] = field(default_factory=_new_log_tail)


#: Shown when the dev servers are up but nothing serves the proxied hostnames. Without this the
#: only symptom is the browser's own ERR_CONNECTION_REFUSED inside the preview iframe, which points
#: at the generated app — the last place the actual problem is.
PROXY_MISSING_WARNING = (
    "The dev servers are running inside the sandbox, but the reverse proxy that serves the preview "
    "URLs is not — the browser will refuse to connect. Start it with `make proxy` (or `make dev` "
    "for the whole stack)."
)

#: Shown when the app database is addressed as loopback. Inside the sandbox `localhost` is the
#: *sandbox*, and its network is `internal`, so there is no route to a database on the host either:
#: the generated backend starts fine and then every query dies with mongoose's
#: "buffering timed out after 10000ms", which reads as a bug in the generated code.
#: Shown when the sandbox is close to its PID ceiling. Worth surfacing *before* the ceiling is hit,
#: because node does not fail gracefully there — it aborts with
#: ``Assertion failed: (0) == (uv_thread_create(...))``, which names neither PIDs nor limits and
#: reads like a native crash (phase-59).
PID_PRESSURE_WARNING = (
    "This sandbox is running {count} processes, close to its limit of {limit}. Orphaned dev "
    "servers are the usual cause; restart the preview, or restart the sandbox if it persists."
)

DB_UNREACHABLE_WARNING = (
    "The generated app's database is configured as {host}, which from inside the sandbox means the "
    "sandbox itself — its queries will time out. Point APP_DB_CLUSTER_URI at a MongoDB the sandbox "
    "can reach (e.g. `mongodb://forgeflow-appdb:27017` from `make dev`), or give the project its "
    "own connection string."
)

#: Shown when the app database is addressed by a docker name that nothing currently answers to.
#: Docker's embedded DNS only holds *running* containers, so the generated backend fails at boot
#: with `getaddrinfo EAI_AGAIN forgeflow-appdb` — a resolver error that reads like a typo in the
#: generated config rather than "that container is not running". The usual cause is a docker
#: restart: the sandbox comes back on demand, its neighbours do not.
DB_HOST_UNRESOLVABLE_WARNING = (
    "The generated app's database host `{host}` does not resolve on the preview network — nothing "
    "is running under that name, so the app fails to connect with `EAI_AGAIN`. Start it with "
    "`make host-deps` (or `make dev` for the whole stack)."
)

#: Hosts that can never mean "the machine running ForgeFlow" from inside a container.
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})


def loopback_db_host(uri: str) -> str | None:
    """The host:port of ``uri`` when it is loopback (so unreachable from a sandbox), else None.

    Only the host is returned — never the credentials a BYO connection string may carry.
    """
    host = urlsplit(uri).hostname or ""
    if host.lower() not in _LOOPBACK_HOSTS:
        return None
    port = urlsplit(uri).port
    return f"{host}:{port}" if port else host


def docker_db_host(uri: str) -> str | None:
    """The bare docker hostname ``uri`` names, or ``None`` when it is not one.

    A single-label host can only be a container name or a network alias: Atlas hostnames, IPv4
    literals and every other routable address carry a dot. Loopback is excluded because it is a
    *different* diagnosis with a different fix (see :func:`loopback_db_host`).

    Only the host is returned — never the credentials a BYO connection string may carry.
    """
    host = urlsplit(uri).hostname or ""
    if not host or host.lower() in _LOOPBACK_HOSTS:
        return None
    if "." in host or ":" in host:
        return None
    return host


@dataclass
class _ProjectPreview:
    procs: dict[PreviewProcess, _Proc] = field(default_factory=dict)
    warning: str | None = None


def preview_urls(project_id: str) -> tuple[str, str]:
    """(fe_url, be_url) for this environment.

    Prod uses wildcard subdomains under ``PREVIEW_BASE_DOMAIN``; locally the Caddy proxy routes
    ``*.preview.localhost`` to the sandbox container by name.
    """
    base = str(get_config().get("preview_base_domain")).strip().strip(".")
    if base:
        return f"https://{project_id}.{base}", f"https://{project_id}.api.{base}"
    return (
        f"http://{project_id}.preview.localhost",
        f"http://{project_id}.api.preview.localhost",
    )


def allowed_preview_hosts(project_id: str) -> list[str]:
    """Hostnames the FE dev server must accept (its Vite ``server.allowedHosts``).

    The proxied preview hostnames, plus the sandbox's container name (how the proxy addresses it)
    and loopback (in-sandbox health probes and Playwright).
    """
    hosts = ["localhost", "127.0.0.1", container_name(project_id)]
    for url in preview_urls(project_id):
        host = urlsplit(url).hostname
        if host and host not in hosts:
            hosts.append(host)
    return hosts


async def _run_to_completion(runtime: WorkspaceRuntime, argv: list[str], cwd: str = "") -> int:
    """Run a short command in the sandbox and return its exit code (drains output)."""
    handle = await asyncio.to_thread(runtime.start_exec, argv, cwd, None)

    def drain() -> int:
        for _ in handle.stream():
            pass
        return handle.wait()

    return await asyncio.to_thread(drain)


async def _run_capture(
    runtime: WorkspaceRuntime, argv: list[str], cwd: str = ""
) -> tuple[int, str]:
    """Run a short command in the sandbox and return ``(exit_code, combined output)``."""
    handle = await asyncio.to_thread(runtime.start_exec, argv, cwd, None)

    def drain() -> tuple[int, str]:
        chunks: list[str] = []
        for chunk in handle.stream():
            chunks.append(chunk.data.decode("utf-8", errors="replace"))
        return handle.wait(), "".join(chunks)

    return await asyncio.to_thread(drain)


class PreviewService:
    def __init__(self, provider: RuntimeProvider | None = None) -> None:
        self._provider = provider
        self._state: dict[str, _ProjectPreview] = {}

    # -- helpers -----------------------------------------------------------------------

    async def _runtime(self, project: Project) -> WorkspaceRuntime:
        provider = self._provider or active_runtime_provider()
        return await provider(project)

    async def _probe(self, runtime: WorkspaceRuntime, proc: _Proc) -> bool:
        argv = [
            "curl",
            "-sf",
            "-o",
            "/dev/null",
            "-m",
            str(_PROBE_TIMEOUT_S),
            f"http://127.0.0.1:{proc.port}{proc.health_path}",
        ]
        try:
            return await _run_to_completion(runtime, argv) == 0
        except Exception:  # a probe must never take the service down
            logger.debug("preview probe failed", exc_info=True)
            return False

    async def _reclaim(self, runtime: WorkspaceRuntime, ports: list[int]) -> None:
        """Guarantee the preview ports are free before anything is started.

        Not an optimisation: the proxy routes to *fixed* ports, so a dev server that slides to the
        next free one is unreachable however healthy it reports itself. Reclaiming by port (rather
        than by the PIDs this process happens to remember) is what makes a restart correct after a
        ``--reload``, a crash, or anything else that emptied ``self._state``.
        """
        script = reclaim_script(ports, kinds=[str(k) for k in PreviewProcess])
        try:
            _code, output = await _run_capture(runtime, ["sh", "-c", script])
        except Exception as exc:  # a sandbox that cannot run sh has bigger problems
            raise SystemError("Could not reclaim the preview ports") from exc

        busy = parse_busy_ports(output)
        if busy:
            # Fail loudly. The alternative -- letting Vite pick 5175 -- reports a healthy preview
            # on a port nothing proxies, which is strictly worse than an error (phase-59).
            raise SystemError(
                "Preview port(s) "
                + ", ".join(str(p) for p in busy)
                + " are still in use inside the sandbox and could not be freed. "
                "Restart the sandbox for this project and try again."
            )

    async def _process_count(self, runtime: WorkspaceRuntime) -> int | None:
        """How many processes the sandbox is running (diagnostics; never fatal)."""
        try:
            _code, output = await _run_capture(runtime, ["sh", "-c", PROCESS_COUNT_SCRIPT])
        except Exception:
            logger.debug("preview process census failed", exc_info=True)
            return None
        return parse_process_count(output)

    async def _pump(self, project_id: str, kind: PreviewProcess, proc: _Proc) -> None:
        """Stream a dev server's output until it exits (it normally never does)."""
        try:
            async for chunk in aiter_chunks(proc.handle):
                text = chunk.data.decode("utf-8", errors="replace")
                # Buffer a bounded tail so a crash leaves diagnosable evidence (phase-55 task 3).
                for line in text.splitlines():
                    proc.log_tail.append(line)
                await emit(
                    project_id,
                    EventType.terminal_output,
                    {
                        "source": "preview",
                        "process": str(kind),
                        "stream": chunk.stream,
                        "chunk": text,
                    },
                )
        finally:
            proc.exited = True
            with contextlib.suppress(Exception):
                proc.exit_code = await asyncio.to_thread(proc.handle.wait)
            # A dev server exiting by itself is a failure worth surfacing immediately.
            await emit(
                project_id,
                EventType.preview_status,
                {"process": str(kind), "status": PreviewStatus.failed, "exit_code": proc.exit_code},
            )

    def _status_of(self, proc: _Proc | None, healthy: bool) -> PreviewStatus:
        if proc is None:
            return PreviewStatus.stopped
        if proc.exited:
            return PreviewStatus.failed
        return PreviewStatus.running if healthy else PreviewStatus.starting

    # -- lifecycle ---------------------------------------------------------------------

    async def start(self, project: Project) -> PreviewInfo:
        project_id = str(project.id)
        config = get_config()
        fe_url, be_url = preview_urls(project_id)

        await self.stop(project, emit_event=False)  # restart semantics: never double-run servers

        runtime = await self._runtime(project)

        # Container-backend only (the local test backend has no docker networking, and no sandbox
        # to install into). Deps come FIRST for two reasons: a freshly instantiated skeleton has no
        # node_modules at all, so `pnpm dev` would exit instantly with "vite: not found"; and the
        # install's egress window is cleanest before the *internal* preview network is attached.
        if isinstance(runtime, DockerRuntime):
            # Before anything is launched: a preview needs the proxy and the generated-app database
            # as much as it needs the sandbox, and only the sandbox comes back by itself after a
            # docker restart. Doing this first also gives mongo the install's duration to finish
            # starting, which is time the generated backend -- one connection attempt, at boot --
            # does not otherwise get.
            if bool(config.get("preview_autostart_deps")):
                await get_manager().start_preview_dependencies()
            await ensure_dependencies(project, runtime)
            await get_manager().attach_preview_network(project_id)

        fe_port = int(config.get("preview_fe_port"))
        be_port = int(config.get("preview_be_port"))

        # Orphans from a previous run own these ports until proven otherwise -- including orphans
        # this process never started, which is the common case after a --reload.
        await self._reclaim(runtime, [be_port, fe_port])

        # The skeleton's env contract (phase-22): BE reads MONGODB_URI/PORT, FE reads
        # VITE_API_BASE_URL. The MONGODB_URI is the project's isolated database — BYO if the owner
        # supplied one, else a per-project DB in the shared cluster (phase-36) — addressed the way a
        # *sandbox* must address it, which is not how the control plane does (APP_DB_SANDBOX_URI).
        # It's a secret, so it's injected into the sandbox env and never logged. Imported lazily to
        # keep the sandbox layer decoupled from the deploy layer.
        from app.deploy.db_provision import DbProvisioner

        be_env = {
            "PORT": str(be_port),
            "NODE_ENV": "development",
            "MONGODB_URI": await DbProvisioner().get_sandbox_mongodb_uri(project),
        }
        fe_env = {
            "NODE_ENV": "development",
            "VITE_API_BASE_URL": be_url,
            # Vite >= 5.4.12 answers 403 to a Host header it does not recognise, and the proxy
            # forwards the *original* host — so the dev server has to be told which ones are ours,
            # or the preview iframe shows a 403 instead of the app. (`*.localhost` is allowed by
            # Vite already; a real PREVIEW_BASE_DOMAIN is not.) The skeleton's vite.config reads it.
            "VITE_ALLOWED_HOSTS": ",".join(allowed_preview_hosts(project_id)),
        }

        state = _ProjectPreview()
        # Nothing inside the sandbox can tell that its *environment* is wrong, so check here: a
        # preview reported "running" that cannot possibly work is the least useful answer available.
        warnings: list[str] = []
        if isinstance(runtime, DockerRuntime):
            if not await get_manager().preview_proxy_attached():
                warnings.append(PROXY_MISSING_WARNING)
                logger.warning("preview started for %s with no reverse proxy running", project_id)
            limit = int(config.get("sandbox_pids_limit"))
            ratio = float(config.get("preview_pid_warn_ratio"))
            count = await self._process_count(runtime)
            if count is not None and ratio > 0 and limit > 0 and count >= limit * ratio:
                warnings.append(PID_PRESSURE_WARNING.format(count=count, limit=limit))
                logger.warning(
                    "sandbox %s is at %d/%d processes before preview start",
                    project_id,
                    count,
                    limit,
                )
            db_host = loopback_db_host(be_env["MONGODB_URI"])
            docker_host = docker_db_host(be_env["MONGODB_URI"])
            if db_host:
                warnings.append(DB_UNREACHABLE_WARNING.format(host=db_host))
                logger.warning(
                    "preview started for %s with an unreachable app database (%s)",
                    project_id,
                    db_host,
                )
            elif docker_host is not None:
                # A name that resolved yesterday resolves to nothing once its container stops, and
                # the only trace is `EAI_AGAIN` in the generated app's own log. Checked after the
                # restart above, so this fires only when starting it could not (or did not) help.
                dns_state = await get_manager().preview_dns_state(docker_host)
                if dns_state in ("stopped", "absent"):
                    warnings.append(DB_HOST_UNRESOLVABLE_WARNING.format(host=docker_host))
                    logger.warning(
                        "preview started for %s with an app database host that does not resolve "
                        "(%s: %s)",
                        project_id,
                        docker_host,
                        dns_state,
                    )
        state.warning = " ".join(warnings) or None
        self._state[project_id] = state

        # Backend first so the frontend has something to talk to on first paint.
        for kind, cmd_key, dir_key, port, env, health in (
            (
                PreviewProcess.backend,
                "preview_be_cmd",
                "preview_be_dir",
                be_port,
                be_env,
                str(config.get("preview_be_health_path")),
            ),
            (
                PreviewProcess.frontend,
                "preview_fe_cmd",
                "preview_fe_dir",
                fe_port,
                fe_env,
                str(config.get("preview_health_path")),
            ),
        ):
            command = str(config.get(cmd_key)).format(port=port).strip()
            if not shlex.split(command):
                raise UserError(f"{cmd_key} is empty")
            # The process records its own PID and then `exec`s, so the pidfile holds the dev server
            # itself -- not a wrapper whose death would leave an unkillable orphan. Written into the
            # workspace, so ownership outlives this control-plane process.
            argv = wrap_with_pidfile(command, str(kind))
            handle = await asyncio.to_thread(
                runtime.start_exec, argv, str(config.get(dir_key)), env
            )
            proc = _Proc(handle=handle, port=port, health_path=health)
            proc.task = asyncio.create_task(self._pump(project_id, kind, proc))
            state.procs[kind] = proc
            await emit(
                project_id,
                EventType.preview_status,
                {"process": str(kind), "status": PreviewStatus.starting, "port": port},
            )

        await self._wait_healthy(runtime, state)
        info = await self.status(project)
        await emit(project_id, EventType.preview_status, info.model_dump(mode="json"))
        return info

    async def _wait_healthy(self, runtime: WorkspaceRuntime, state: _ProjectPreview) -> None:
        deadline = time.monotonic() + float(get_config().get("preview_health_timeout_s"))
        while time.monotonic() < deadline:
            if any(p.exited for p in state.procs.values()):
                return  # fail fast: a crashed server will never become healthy
            probes = [await self._probe(runtime, p) for p in state.procs.values()]
            if probes and all(probes):
                return
            await asyncio.sleep(0.5)

    async def stop(self, project: Project, emit_event: bool = True) -> PreviewInfo:
        project_id = str(project.id)
        state = self._state.pop(project_id, None)
        if state is not None:
            for proc in state.procs.values():
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(proc.handle.kill)
                if proc.task is not None:
                    proc.task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await proc.task

            runtime_backed = self._provider or active_runtime_provider()
            with contextlib.suppress(Exception):
                runtime = await runtime_backed(project)
                if isinstance(runtime, DockerRuntime):
                    await get_manager().detach_preview_network(project_id)

        info = PreviewInfo(project_id=project_id)
        if emit_event:
            await emit(project_id, EventType.preview_status, info.model_dump(mode="json"))
        return info

    async def restart(self, project: Project) -> PreviewInfo:
        return await self.start(project)

    def log_tail(self, project: Project, process: PreviewProcess) -> str:
        """The recent output of a preview process — evidence for a boot-failure diagnosis.

        Bounded by ``preview_log_tail_lines``; empty when the process is not (or no longer)
        tracked. Synchronous — it reads an in-memory ring, no sandbox round-trip (phase-55 task 3).
        """
        state = self._state.get(str(project.id))
        if state is None:
            return ""
        proc = state.procs.get(process)
        return "\n".join(proc.log_tail) if proc is not None else ""

    async def status(self, project: Project) -> PreviewInfo:
        project_id = str(project.id)
        fe_url, be_url = preview_urls(project_id)
        state = self._state.get(project_id)
        if state is None or not state.procs:
            return PreviewInfo(project_id=project_id)

        runtime = await self._runtime(project)
        statuses: dict[PreviewProcess, PreviewStatus] = {}
        for kind, proc in state.procs.items():
            healthy = False if proc.exited else await self._probe(runtime, proc)
            statuses[kind] = self._status_of(proc, healthy)

        fe_status = statuses.get(PreviewProcess.frontend, PreviewStatus.stopped)
        be_status = statuses.get(PreviewProcess.backend, PreviewStatus.stopped)
        return PreviewInfo(
            project_id=project_id,
            fe_url=fe_url if fe_status != PreviewStatus.stopped else None,
            be_url=be_url if be_status != PreviewStatus.stopped else None,
            fe_status=fe_status,
            be_status=be_status,
            warning=state.warning,
        )


_service: PreviewService | None = None


def get_preview_service() -> PreviewService:
    global _service
    if _service is None:
        _service = PreviewService()
    return _service


def set_preview_service(service: PreviewService | None) -> None:
    global _service
    _service = service


def reset_preview_service() -> None:
    global _service
    _service = None
