"""Live preview: run the generated app's dev servers inside its sandbox (phase-15).

Starts the skeleton's FE (Vite) and BE (Express) via phase-13 exec, streams their logs as
``terminal.output`` tagged per process, health-polls both, and resolves the proxied URLs.

Two things worth knowing:

- **Health probes run inside the sandbox** (``curl`` against loopback), not from the control
  plane. That keeps the check working regardless of how the sandbox is (or isn't) networked, and
  it needs no host access.
- **Networking** is the one deliberate relaxation of phase-11's ``network_mode=none``: on start the
  sandbox joins a dedicated *internal* docker network (see ``build_preview_network_kwargs``) so the
  reverse proxy can reach the dev servers. There is still no host network and no internet.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shlex
import time
from dataclasses import dataclass, field

from app.core.config import get_config
from app.core.errors import UserError
from app.db.models import Project
from app.realtime.hub import emit
from app.realtime.schemas import EventType
from app.sandbox.exec import aiter_chunks
from app.sandbox.manager import get_manager
from app.sandbox.runtime import DockerRuntime, ExecHandle, WorkspaceRuntime
from app.sandbox.schemas import PreviewInfo, PreviewProcess, PreviewStatus
from app.sandbox.workspace import RuntimeProvider, active_runtime_provider

logger = logging.getLogger(__name__)

_PROBE_TIMEOUT_S = 2


@dataclass
class _Proc:
    handle: ExecHandle
    port: int
    health_path: str
    task: asyncio.Task[None] | None = None
    exited: bool = False
    exit_code: int | None = None


@dataclass
class _ProjectPreview:
    procs: dict[PreviewProcess, _Proc] = field(default_factory=dict)


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


async def _run_to_completion(runtime: WorkspaceRuntime, argv: list[str], cwd: str = "") -> int:
    """Run a short command in the sandbox and return its exit code (drains output)."""
    handle = await asyncio.to_thread(runtime.start_exec, argv, cwd, None)

    def drain() -> int:
        for _ in handle.stream():
            pass
        return handle.wait()

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

    async def _pump(self, project_id: str, kind: PreviewProcess, proc: _Proc) -> None:
        """Stream a dev server's output until it exits (it normally never does)."""
        try:
            async for chunk in aiter_chunks(proc.handle):
                await emit(
                    project_id,
                    EventType.terminal_output,
                    {
                        "source": "preview",
                        "process": str(kind),
                        "stream": chunk.stream,
                        "chunk": chunk.data.decode("utf-8", errors="replace"),
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

        # Let the proxy reach the dev servers. Only meaningful for the container backend.
        if isinstance(runtime, DockerRuntime):
            await get_manager().attach_preview_network(project_id)

        fe_port = int(config.get("preview_fe_port"))
        be_port = int(config.get("preview_be_port"))

        # The skeleton's env contract (phase-22): BE reads MONGODB_URI/PORT, FE reads
        # VITE_API_BASE_URL. Real per-project DB provisioning lands in phase-36.
        be_env = {
            "PORT": str(be_port),
            "NODE_ENV": "development",
            "MONGODB_URI": f"{config.get('mongodb_uri')}/{project.app_db_name}",
        }
        fe_env = {"NODE_ENV": "development", "VITE_API_BASE_URL": be_url}

        state = _ProjectPreview()
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
            argv = shlex.split(str(config.get(cmd_key)).format(port=port))
            if not argv:
                raise UserError(f"{cmd_key} is empty")
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
