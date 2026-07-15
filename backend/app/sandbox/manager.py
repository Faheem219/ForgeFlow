"""Per-project sandbox container lifecycle via the Docker SDK (phase-11).

Responsibilities:
  - map ``Project ↔ container`` (persisted on ``Project.sandbox_id``);
  - create/start/stop/destroy the container with the hard isolation posture applied at
    ``docker run`` time (no host network, dropped caps, cpu/mem/pid limits, read-only root
    with a writable ``/workspace`` named volume + ``/tmp`` tmpfs);
  - reap idle containers after ``sandbox_idle_timeout_s`` and reconcile mappings on startup.

The Docker SDK is synchronous; every blocking call is off-loaded via :func:`asyncio.to_thread`
so the event loop is never blocked. The client is injectable for tests (a fake stands in for
the daemon); in production it is built lazily from the environment.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from docker.errors import APIError, DockerException, NotFound

# SystemError shadows the builtin (taxonomy name fixed by the plan); A004 suppressed on-line.
from app.core.config import get_config
from app.core.errors import SystemError  # noqa: A004
from app.db.models import Project
from app.db.models.common import utcnow
from app.db.repos import ProjectRepo
from app.realtime.hub import emit
from app.realtime.schemas import EventType
from app.sandbox.schemas import SandboxInfo, SandboxState

logger = logging.getLogger(__name__)

WORKSPACE = "/workspace"
LABEL_MANAGED = "forgeflow.managed"
LABEL_PROJECT = "forgeflow.project"
LABEL_ROLE = "forgeflow.role"

# Docker container.status → our coarse runtime state.
_STARTING = {"created", "restarting"}
_STOPPED = {"exited", "paused", "dead", "removing"}


def container_name(project_id: str) -> str:
    return f"forgeflow-sb-{project_id}"


def volume_name(project_id: str) -> str:
    return f"forgeflow-vol-{project_id}"


def _map_status(docker_status: str) -> SandboxState:
    if docker_status == "running":
        return SandboxState.running
    if docker_status in _STARTING:
        return SandboxState.starting
    if docker_status in _STOPPED:
        return SandboxState.stopped
    return SandboxState.error


def build_run_kwargs(
    project_id: str,
    *,
    image: str,
    cpu_limit: float,
    mem_limit: str,
    pids_limit: int,
    read_only: bool,
) -> dict[str, Any]:
    """The ``docker run`` spec — isolation posture lives here (D5, §7).

    Pure + side-effect free so the isolation guarantees are unit-testable without a daemon.
    """
    return {
        "image": image,
        "name": container_name(project_id),
        "hostname": "sandbox",
        "detach": True,
        # No host network by default; preview networking is added explicitly in phase-15.
        "network_mode": "none",
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges"],
        "read_only": read_only,
        "pids_limit": pids_limit,
        "mem_limit": mem_limit,
        "nano_cpus": int(cpu_limit * 1_000_000_000),
        # Writable scratch even with a read-only root; /workspace persistence is the named volume.
        "tmpfs": {"/tmp": ""},
        "volumes": {volume_name(project_id): {"bind": WORKSPACE, "mode": "rw"}},
        "labels": {LABEL_MANAGED: "true", LABEL_PROJECT: project_id},
        "restart_policy": {"Name": "no"},
    }


def build_preview_network_kwargs(name: str) -> dict[str, Any]:
    """The ``docker network create`` spec for live preview (phase-15).

    ``internal=True`` is the whole point: containers on this network can reach *each other*
    (proxy → sandbox, and the browser's FE → BE calls land via the proxy), but the network has
    **no route to the host or the internet**. This is a deliberate, minimal relaxation of the
    ``network_mode=none`` default from phase-11 — never the host network. Flagged for the
    phase-47 hardening review.

    Pure + side-effect free so the isolation guarantee is unit-testable without a daemon.
    """
    return {
        "name": name,
        "driver": "bridge",
        "internal": True,
        "check_duplicate": True,
        "labels": {LABEL_MANAGED: "true", LABEL_ROLE: "preview"},
    }


class SandboxManager:
    """Owns container lifecycle + idle-activity tracking for all projects on this instance."""

    def __init__(self, client: Any | None = None, projects: ProjectRepo | None = None) -> None:
        self._client_cache: Any | None = client
        self._projects = projects or ProjectRepo()
        # project_id → last-activity monotonic timestamp (drives idle reaping).
        self._activity: dict[str, float] = {}

    # -- docker plumbing ---------------------------------------------------------------

    def _client(self) -> Any:
        if self._client_cache is None:
            try:
                import docker

                self._client_cache = docker.from_env()
            except DockerException as exc:  # daemon down / socket missing
                raise SystemError("Sandbox runtime unavailable") from exc
        return self._client_cache

    async def _get_container(self, project_id: str) -> Any | None:
        client = self._client()
        try:
            return await asyncio.to_thread(client.containers.get, container_name(project_id))
        except NotFound:
            return None

    async def get_container(self, project_id: str) -> Any | None:
        """The running container object for a project, or ``None`` (used by the FS layer)."""
        return await self._get_container(project_id)

    async def _ensure_volume(self, project_id: str) -> None:
        client = self._client()
        name = volume_name(project_id)
        try:
            await asyncio.to_thread(client.volumes.get, name)
        except NotFound:
            await asyncio.to_thread(
                client.volumes.create,
                name=name,
                labels={LABEL_MANAGED: "true", LABEL_PROJECT: project_id},
            )

    async def _create_container(self, project_id: str) -> Any:
        client = self._client()
        config = get_config()
        kwargs = build_run_kwargs(
            project_id,
            image=str(config.get("sandbox_image")),
            cpu_limit=float(config.get("sandbox_cpu_limit")),
            mem_limit=str(config.get("sandbox_mem_limit")),
            pids_limit=int(config.get("sandbox_pids_limit")),
            read_only=bool(config.get("sandbox_read_only_root")),
        )
        try:
            return await asyncio.to_thread(client.containers.run, **kwargs)
        except APIError as exc:
            raise SystemError("Failed to create sandbox container") from exc

    async def _stop_container(self, project_id: str) -> bool:
        container = await self._get_container(project_id)
        if container is None:
            return False
        await asyncio.to_thread(container.stop, timeout=10)
        return True

    # -- preview network (phase-15) ----------------------------------------------------

    async def ensure_preview_network(self) -> Any:
        """Get-or-create the shared internal preview network."""
        client = self._client()
        name = str(get_config().get("preview_network_name"))
        try:
            return await asyncio.to_thread(client.networks.get, name)
        except NotFound:
            kwargs = build_preview_network_kwargs(name)
            try:
                return await asyncio.to_thread(client.networks.create, **kwargs)
            except APIError as exc:
                raise SystemError("Failed to create the preview network") from exc

    async def attach_preview_network(self, project_id: str) -> bool:
        """Join the sandbox to the preview network so the proxy can reach its dev servers."""
        container = await self._get_container(project_id)
        if container is None:
            return False
        network = await self.ensure_preview_network()
        try:
            await asyncio.to_thread(network.connect, container.id)
        except APIError:
            # Already connected — docker reports a 403/409 here; joining twice is a no-op for us.
            logger.debug("sandbox %s already on the preview network", project_id, exc_info=True)
        return True

    async def detach_preview_network(self, project_id: str) -> None:
        """Drop the sandbox back to no-network once preview stops."""
        container = await self._get_container(project_id)
        if container is None:
            return
        client = self._client()
        name = str(get_config().get("preview_network_name"))
        try:
            network = await asyncio.to_thread(client.networks.get, name)
        except NotFound:
            return
        try:
            await asyncio.to_thread(network.disconnect, container.id, force=True)
        except APIError:
            logger.debug("sandbox %s was not on the preview network", project_id, exc_info=True)

    # -- activity / reaping ------------------------------------------------------------

    def touch(self, project_id: str, now: float | None = None) -> None:
        """Mark the project's sandbox as active (resets its idle timer)."""
        self._activity[project_id] = now if now is not None else time.monotonic()

    def _reap_interval(self) -> int:
        return int(get_config().get("sandbox_reap_interval_s"))

    async def reap_idle_once(
        self, now: float | None = None, idle_timeout: int | None = None
    ) -> list[str]:
        """Stop every container idle beyond the timeout. Returns the reaped project ids."""
        now = now if now is not None else time.monotonic()
        timeout = (
            idle_timeout
            if idle_timeout is not None
            else int(get_config().get("sandbox_idle_timeout_s"))
        )
        reaped: list[str] = []
        for project_id, last in list(self._activity.items()):
            if now - last < timeout:
                continue
            try:
                stopped = await self._stop_container(project_id)
            except (DockerException, SystemError):
                logger.warning("idle reap failed for %s", project_id, exc_info=True)
                continue
            self._activity.pop(project_id, None)
            if stopped:
                reaped.append(project_id)
                await emit(
                    project_id,
                    EventType.sandbox_status,
                    {"status": SandboxState.stopped, "reason": "idle"},
                )
        return reaped

    async def run_reaper(
        self, *, interval: int | None = None, stop_event: asyncio.Event | None = None
    ) -> None:
        """Background sweep loop; resilient to per-iteration docker errors."""
        while True:
            try:
                await self.reap_idle_once()
            except Exception:  # never let the reaper die on a transient error
                logger.warning("sandbox reaper iteration failed", exc_info=True)
            wait_s = interval if interval is not None else self._reap_interval()
            if stop_event is None:
                await asyncio.sleep(wait_s)
                continue
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=wait_s)
                return  # shutdown requested
            except TimeoutError:
                continue

    # -- lifecycle API -----------------------------------------------------------------

    async def ensure(self, project: Project) -> SandboxInfo:
        """Create-if-absent, start-if-stopped. Persists ``sandbox_id``; idempotent when running."""
        project_id = str(project.id)
        container = await self._get_container(project_id)
        created = False
        if container is None:
            await self._ensure_volume(project_id)
            container = await self._create_container(project_id)
            created = True
        else:
            await asyncio.to_thread(container.reload)
            if container.status != "running":
                await asyncio.to_thread(container.start)

        container_id = container.id
        if project.sandbox_id != container_id:
            project.sandbox_id = container_id
            project.updated_at = utcnow()
            await project.save()

        self.touch(project_id)
        await emit(
            project_id,
            EventType.sandbox_status,
            {"status": SandboxState.running, "created": created, "container_id": container_id},
        )
        return SandboxInfo(
            project_id=project_id,
            status=SandboxState.running,
            container_id=container_id,
            container_name=container_name(project_id),
            image=str(get_config().get("sandbox_image")),
            volume=volume_name(project_id),
        )

    async def stop(self, project: Project) -> SandboxInfo:
        project_id = str(project.id)
        await self._stop_container(project_id)
        self._activity.pop(project_id, None)
        await emit(
            project_id, EventType.sandbox_status, {"status": SandboxState.stopped, "reason": "user"}
        )
        return await self.status(project)

    async def destroy(self, project: Project, remove_volume: bool = False) -> SandboxInfo:
        """Remove the container (and optionally its volume). Clears ``sandbox_id``."""
        project_id = str(project.id)
        container = await self._get_container(project_id)
        if container is not None:
            await asyncio.to_thread(container.remove, force=True)
        if remove_volume:
            await self._remove_volume(project_id)
        self._activity.pop(project_id, None)
        if project.sandbox_id is not None:
            project.sandbox_id = None
            project.updated_at = utcnow()
            await project.save()
        await emit(
            project_id,
            EventType.sandbox_status,
            {"status": SandboxState.absent, "volume_removed": remove_volume},
        )
        return SandboxInfo(
            project_id=project_id, status=SandboxState.absent, volume=volume_name(project_id)
        )

    async def _remove_volume(self, project_id: str) -> None:
        client = self._client()
        try:
            volume = await asyncio.to_thread(client.volumes.get, volume_name(project_id))
        except NotFound:
            return
        await asyncio.to_thread(volume.remove, force=True)

    async def status(self, project: Project) -> SandboxInfo:
        project_id = str(project.id)
        container = await self._get_container(project_id)
        if container is None:
            return SandboxInfo(
                project_id=project_id,
                status=SandboxState.absent,
                volume=volume_name(project_id),
            )
        await asyncio.to_thread(container.reload)
        return SandboxInfo(
            project_id=project_id,
            status=_map_status(container.status),
            container_id=container.id,
            container_name=container_name(project_id),
            image=str(get_config().get("sandbox_image")),
            volume=volume_name(project_id),
        )

    async def reconcile(self) -> None:
        """Startup reconciliation: adopt existing managed containers, clear stale mappings."""
        client = self._client()
        containers = await asyncio.to_thread(
            client.containers.list, all=True, filters={"label": f"{LABEL_MANAGED}=true"}
        )
        by_project: dict[str, Any] = {}
        for container in containers:
            pid = container.labels.get(LABEL_PROJECT)
            if pid:
                by_project[pid] = container

        for project in await self._projects.all():
            project_id = str(project.id)
            container = by_project.get(project_id)
            if container is not None:
                if project.sandbox_id != container.id:
                    project.sandbox_id = container.id
                    project.updated_at = utcnow()
                    await project.save()
                if container.status == "running":
                    self.touch(project_id)
            elif project.sandbox_id is not None:
                # The mapped container is gone; drop the stale pointer so ensure recreates it.
                project.sandbox_id = None
                project.updated_at = utcnow()
                await project.save()


_manager: SandboxManager | None = None


def get_manager() -> SandboxManager:
    """Return the process-wide sandbox manager (built once)."""
    global _manager
    if _manager is None:
        _manager = SandboxManager()
    return _manager


def set_manager(manager: SandboxManager | None) -> None:
    """Install a specific manager instance (tests inject one backed by a fake docker client)."""
    global _manager
    _manager = manager


def reset_manager() -> None:
    """Drop the cached manager (test helper)."""
    global _manager
    _manager = None
