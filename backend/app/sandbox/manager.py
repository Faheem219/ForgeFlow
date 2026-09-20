"""Per-project sandbox container lifecycle via the Docker SDK (phase-11).

Responsibilities:
  - map ``Project ↔ container`` (persisted on ``Project.sandbox_id``);
  - create/start/stop/destroy the container with the hard isolation posture applied at
    ``docker run`` time (its own *internal* per-project network — no host network, no internet —
    dropped caps, cpu/mem/pid limits, read-only root with writable ``/workspace`` + ``/home/app``
    named volumes and a ``/tmp`` tmpfs);
  - recreate a container that predates the current image/mount spec, so an image fix actually
    reaches projects that already have a sandbox;
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

from docker.errors import APIError, DockerException, ImageNotFound, NotFound

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
#: The non-root user's home *inside the image* (see ``sandbox/Dockerfile``). Package managers all
#: write under it, so it is mounted from a writable volume — the root filesystem is read-only.
SANDBOX_HOME = "/home/app"
#: Mounts a usable sandbox must have. A container missing one predates the current spec and is
#: recreated on the next ``ensure`` (see :meth:`SandboxManager._is_stale`).
REQUIRED_MOUNTS = frozenset({WORKSPACE, SANDBOX_HOME})
LABEL_MANAGED = "forgeflow.managed"
LABEL_PROJECT = "forgeflow.project"
LABEL_ROLE = "forgeflow.role"

# Docker container.status → our coarse runtime state.
_STARTING = {"created", "restarting"}
_STOPPED = {"exited", "paused", "dead", "removing"}


#: Every sandbox container carries this prefix — which is also how the shared dev-stack services
#: (the proxy, the generated apps' database) are told apart from sandboxes on the preview network.
SANDBOX_NAME_PREFIX = "forgeflow-sb-"


def container_name(project_id: str) -> str:
    return f"{SANDBOX_NAME_PREFIX}{project_id}"


def _networks_of(container: object) -> dict[str, Any]:
    """The networks a container is *configured* for — populated even while it is stopped."""
    settings = (getattr(container, "attrs", None) or {}).get("NetworkSettings") or {}
    return settings.get("Networks") or {}


def _is_sandbox(container: object) -> bool:
    return str(getattr(container, "name", "")).startswith(SANDBOX_NAME_PREFIX)


def volume_name(project_id: str) -> str:
    return f"forgeflow-vol-{project_id}"


def home_volume_name(project_id: str) -> str:
    """The per-project volume mounted at :data:`SANDBOX_HOME` (package-manager caches)."""
    return f"forgeflow-home-{project_id}"


def sandbox_network_name(project_id: str) -> str:
    """The per-project ``internal`` network a sandbox is created on (its isolated default)."""
    return f"forgeflow-sbnet-{project_id}"


#: Docker's wording when every subnet in its configured address pools is taken.
_POOL_EXHAUSTED_MARKERS = ("address pools", "no available", "pool overlaps")


def _is_pool_exhausted(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _POOL_EXHAUSTED_MARKERS)


#: Docker's wording when a container's configured network has been removed underneath it:
#: "failed to set up container networking: network <id> not found".
def _is_missing_network(exc: Exception) -> bool:
    text = str(exc).lower()
    return "network" in text and "not found" in text


#: Docker's wording when the image is neither on this machine nor pullable. `containers.run`
#: falls back to a registry pull, so a *locally built* image that was never built (or was built
#: under a different tag) surfaces as a pull failure rather than a plain 404.
_MISSING_IMAGE_MARKERS = (
    "no such image",
    "pull access denied",
    "manifest unknown",
    "manifest for",
    "not found: manifest",
    "repository does not exist",
)


def _is_missing_image(exc: Exception) -> bool:
    if isinstance(exc, ImageNotFound):
        return True
    text = str(exc).lower()
    return any(marker in text for marker in _MISSING_IMAGE_MARKERS)


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
        # The sandbox's own *internal* network (see build_sandbox_network_kwargs): no route to the
        # host and no internet, and nothing else on it — the isolation of `network_mode=none` with
        # one difference that matters: docker REFUSES to attach any network to a container in
        # `none` mode ("cannot be connected to multiple networks with one of the networks in
        # private (none) mode"), which made the preview attach (phase-15) and the egress window
        # (phase-39 / dependency installs) silently impossible. Additional networks are still only
        # ever attached deliberately and temporarily.
        "network_mode": sandbox_network_name(project_id),
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges"],
        "read_only": read_only,
        "pids_limit": pids_limit,
        "mem_limit": mem_limit,
        "nano_cpus": int(cpu_limit * 1_000_000_000),
        # Writable scratch even with a read-only root; /workspace persistence is the named volume.
        "tmpfs": {"/tmp": ""},
        "volumes": {
            volume_name(project_id): {"bind": WORKSPACE, "mode": "rw"},
            # corepack/pnpm/npm all write under $HOME, which the read-only root forbids — that is
            # what made every `pnpm` call die with ENOENT on /home/app/.cache/node/corepack. A
            # named volume keeps those caches writable *and* persistent (the pnpm store survives a
            # restart, so a re-install needs no registry) without weakening the read-only root or
            # spending the container's memory budget on a tmpfs.
            home_volume_name(project_id): {"bind": SANDBOX_HOME, "mode": "rw"},
        },
        "labels": {LABEL_MANAGED: "true", LABEL_PROJECT: project_id},
        "restart_policy": {"Name": "no"},
    }


def build_sandbox_network_kwargs(name: str) -> dict[str, Any]:
    """The ``docker network create`` spec for a sandbox's own default network.

    ``internal=True`` is the isolation: no route to the host and none to the internet. It is
    **per project**, so — unlike the shared preview network — one sandbox cannot reach another's
    processes. This is the network a sandbox lives on for its whole life; preview and egress are
    attached on top, deliberately and temporarily.

    Pure + side-effect free so the isolation guarantee is unit-testable without a daemon.
    """
    return {
        "name": name,
        "driver": "bridge",
        "internal": True,
        "check_duplicate": True,
        "labels": {LABEL_MANAGED: "true", LABEL_ROLE: "sandbox"},
    }


def build_preview_network_kwargs(name: str) -> dict[str, Any]:
    """The ``docker network create`` spec for live preview (phase-15).

    ``internal=True`` is the whole point: containers on this network can reach *each other*
    (proxy → sandbox, and the browser's FE → BE calls land via the proxy), but the network has
    **no route to the host or the internet**. This is a deliberate, minimal relaxation of the
    sealed per-project default from phase-11 — never the host network. Flagged for the
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


def build_egress_network_kwargs(name: str) -> dict[str, Any]:
    """The ``docker network create`` spec for live validation (phase-39).

    Unlike the preview network this one is **not** ``internal``: it routes out, because live
    validation drives Playwright against the *deployed public URL* and there is no other way to
    reach it. That makes this the widest relaxation of the sealed-by-default posture in the
    system, so it is deliberately narrow in time rather than in reach: the sandbox is attached for
    one live run and detached in a ``finally`` (see :class:`~app.testing.live.LiveTestRunner`), and
    it is still never the *host* network — generated code gets a routed bridge, not the host
    namespace. Flagged for the phase-47 hardening review.

    Pure + side-effect free so the isolation properties are unit-testable without a daemon.
    """
    return {
        "name": name,
        "driver": "bridge",
        "internal": False,  # the point: live tests must reach the deployed URL
        "check_duplicate": True,
        "labels": {LABEL_MANAGED: "true", LABEL_ROLE: "egress"},
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

    async def container_state(self, project_id: str) -> str:
        """A coarse, independent liveness signal: ``running`` | ``exited`` | ``missing``.

        Read straight from docker's ``container.status``, so a failure classifier keyed on exit
        codes has a second, orthogonal source of truth — an exit code can be stale or unknown
        (a dead container reports ``ExitCode: null``), the container's own status cannot
        (phase-55 task 5).
        """
        container = await self._get_container(project_id)
        if container is None:
            return "missing"
        return "running" if container.status == "running" else "exited"

    async def _ensure_volumes(self, project_id: str) -> None:
        """Create the workspace + home volumes if absent (both outlive any single container)."""
        client = self._client()
        for name in (volume_name(project_id), home_volume_name(project_id)):
            try:
                await asyncio.to_thread(client.volumes.get, name)
            except NotFound:
                await asyncio.to_thread(
                    client.volumes.create,
                    name=name,
                    labels={LABEL_MANAGED: "true", LABEL_PROJECT: project_id},
                )

    async def _ensure_network(self, project_id: str) -> str:
        """Get-or-create the project's own internal network. Returns its name.

        Docker's default address pools only hold ~30 bridge networks, so a pool exhaustion is a
        *when* on a busy host: recover by pruning networks nothing is attached to and retrying
        once, rather than leaving the project without a sandbox.
        """
        client = self._client()
        name = sandbox_network_name(project_id)
        try:
            await asyncio.to_thread(client.networks.get, name)
        except NotFound:
            spec = build_sandbox_network_kwargs(name)
            try:
                await asyncio.to_thread(client.networks.create, **spec)
            except APIError as exc:
                if not _is_pool_exhausted(exc):
                    raise SystemError("Failed to create the sandbox network") from exc
                logger.warning("docker address pools are full; pruning orphaned networks")
                await self.prune_orphan_networks()
                try:
                    await asyncio.to_thread(client.networks.create, **spec)
                except APIError as retry_exc:
                    raise SystemError(
                        "Docker has no free subnets left for a new sandbox. Remove unused "
                        "sandboxes (`make sandbox-reap-all`) or widen `default-address-pools` "
                        "in the docker daemon config."
                    ) from retry_exc
        return name

    async def _remove_network(self, project_id: str) -> None:
        client = self._client()
        try:
            network = await asyncio.to_thread(client.networks.get, sandbox_network_name(project_id))
        except NotFound:
            return
        try:
            await asyncio.to_thread(network.remove)
        except APIError:  # still in use by something we don't own — leave it
            logger.debug("sandbox network for %s could not be removed", project_id, exc_info=True)

    async def _is_stale(self, container: Any, image: str) -> bool:
        """True when an existing container predates the current image / mount / network spec.

        Sandboxes outlive image rebuilds: an adopted container keeps whatever environment it was
        created with, so a fixed image (or a newly added mount, like the writable ``$HOME``) would
        never reach a project that already has a container. Recreating is cheap and safe — the code
        lives on the named volumes, not in the container.
        """
        attrs = getattr(container, "attrs", None) or {}
        mounts = {m.get("Destination") for m in attrs.get("Mounts") or []}
        if not REQUIRED_MOUNTS.issubset(mounts):
            return True
        # A container still on the old `none` posture can never be attached to the preview or egress
        # networks, so it can neither be previewed nor install dependencies. Replace it.
        network_mode = str((attrs.get("HostConfig") or {}).get("NetworkMode") or "")
        project_id = str(container.labels.get(LABEL_PROJECT) or "")
        if network_mode and project_id and network_mode != sandbox_network_name(project_id):
            return True
        current = attrs.get("Image")
        if not current:
            return False
        try:  # a rebuilt `:latest` keeps its tag but gets a new image id
            desired = await asyncio.to_thread(self._client().images.get, image)
        except (NotFound, APIError, DockerException):
            return False  # can't tell → leave the sandbox alone rather than churn it
        return bool(desired.id != current)

    async def _create_container(self, project_id: str) -> Any:
        client = self._client()
        config = get_config()
        # Self-sufficient: a container cannot start without its volumes + its own network, so every
        # path that creates one gets them, not just `ensure`.
        await self._ensure_volumes(project_id)
        await self._ensure_network(project_id)
        kwargs = build_run_kwargs(
            project_id,
            image=str(config.get("sandbox_image")),
            cpu_limit=float(config.get("sandbox_cpu_limit")),
            mem_limit=str(config.get("sandbox_mem_limit")),
            pids_limit=int(config.get("sandbox_pids_limit")),
            read_only=bool(config.get("sandbox_read_only_root")),
        )
        image = str(kwargs["image"])
        try:
            return await asyncio.to_thread(client.containers.run, **kwargs)
        except APIError as exc:
            # The daemon's own reason never reaches the user (SystemError must not leak
            # internals), so log it — it is the only place the real cause is recorded.
            logger.warning("sandbox create failed for %s: %s", project_id, exc)
            if _is_missing_image(exc):
                raise SystemError(
                    f"The sandbox image '{image}' is not on this machine. It is built "
                    "locally, not pulled: run `make sandbox-build` (or set SANDBOX_IMAGE to "
                    "a tag you already have), then try again."
                ) from exc
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

    async def preview_proxy_attached(self) -> bool:
        """Whether something that is *not* a sandbox sits on the preview network — i.e. the proxy.

        The preview URLs (``*.preview.localhost``) are served by that proxy and nothing else, so
        without it the browser cannot connect no matter how healthy the dev servers are. Checking
        by exclusion keeps this honest whatever the proxy container ends up being called.
        """
        client = self._client()
        name = str(get_config().get("preview_network_name"))
        try:
            network = await asyncio.to_thread(client.networks.get, name)
            await asyncio.to_thread(network.reload)
        except (NotFound, DockerException, APIError):
            return False
        attached = (getattr(network, "attrs", None) or {}).get("Containers") or {}
        return any(
            not str(info.get("Name", "")).startswith(SANDBOX_NAME_PREFIX)
            for info in attached.values()
        )

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

    # -- preview dependencies (proxy + generated-app database) -------------------------

    async def _preview_dependencies(self) -> list[Any]:
        """Every container configured for the preview network that is not a sandbox.

        Two of them exist and a preview needs both: the Caddy proxy (the only route into a sandbox)
        and the MongoDB the generated apps use. Identified by exclusion — the same rule
        :meth:`preview_proxy_attached` applies — so this keeps working whatever compose names them.
        """
        client = self._client()
        try:
            containers = await asyncio.to_thread(client.containers.list, all=True)
        except (DockerException, APIError):
            logger.debug("could not list containers for the preview dependencies", exc_info=True)
            return []
        network = str(get_config().get("preview_network_name"))
        return [c for c in containers if not _is_sandbox(c) and network in _networks_of(c)]

    async def start_preview_dependencies(self) -> list[str]:
        """Restart the preview's stopped dependencies. Returns the names started.

        Restarting docker leaves these two exited: they are compose services whose restart policy
        does not survive a daemon restart, and nothing re-runs compose afterwards. The *sandbox*
        meanwhile comes back on its own, because :meth:`ensure` starts it on demand — so the dev
        servers run inside a sandbox whose two neighbours are gone, and the two symptoms that
        produces both point away from the cause: the preview URL refuses to connect (no proxy) and
        the generated backend dies on ``getaddrinfo EAI_AGAIN forgeflow-appdb`` (no database, so no
        DNS record for it either). One cause, one fix — start them again.

        Best effort: a dependency that cannot be started is logged, and the preflight warnings in
        :mod:`app.sandbox.preview` still tell the user which one is missing.
        """
        started: list[str] = []
        for container in await self._preview_dependencies():
            if getattr(container, "status", "") == "running":
                continue
            try:
                await asyncio.to_thread(container.start)
            except (DockerException, APIError):
                logger.warning(
                    "could not start preview dependency %s", container.name, exc_info=True
                )
                continue
            started.append(str(container.name))
        if started:
            logger.info("restarted preview dependencies: %s", ", ".join(started))
            await self._await_dependencies_ready(started)
        return started

    async def _await_dependencies_ready(self, names: list[str]) -> None:
        """Wait (bounded) for just-started dependencies to actually serve.

        Load-bearing for the database: mongo needs a moment before it accepts connections, and the
        generated backend connects exactly **once**, at boot (``templates/app-skeleton`` starts the
        HTTP server first and then dials mongo, logging a failure rather than retrying). Launch the
        dev servers into a database that is still starting and the user reads a connection error
        that is already stale by the time they see it.
        """
        timeout_s = float(get_config().get("preview_deps_ready_timeout_s"))
        deadline = time.monotonic() + timeout_s
        pending = set(names)
        while pending and time.monotonic() < deadline:
            for name in sorted(pending):
                if await self._dependency_ready(name):
                    pending.discard(name)
            if pending:
                await asyncio.sleep(0.5)
        if pending:
            logger.warning(
                "preview dependencies still not ready after %.0fs: %s",
                timeout_s,
                ", ".join(sorted(pending)),
            )

    async def _dependency_ready(self, name: str) -> bool:
        """Whether one dependency is serving — healthy when it declares a healthcheck, else up."""
        client = self._client()
        try:
            container = await asyncio.to_thread(client.containers.get, name)
            await asyncio.to_thread(container.reload)
        except (NotFound, DockerException, APIError):
            return True  # gone or unreadable: there is nothing left to wait for
        state = (getattr(container, "attrs", None) or {}).get("State") or {}
        health = ((state.get("Health") or {}).get("Status") or "").strip()
        if health:
            return health == "healthy"
        return str(getattr(container, "status", "")) == "running"

    async def preview_dns_state(self, host: str) -> str:
        """Whether ``host`` resolves on the preview network: running | stopped | absent | unknown.

        Docker's embedded DNS answers for a container's name and its network aliases, and only
        while that container is **running** — a stopped one is not in the zone at all, which is why
        a generated app gets ``EAI_AGAIN`` (name resolution failed) rather than a refused
        connection. Distinguishing *stopped* from *absent* matters: one is recoverable here, the
        other means the container was never created and only the user can fix it.
        """
        client = self._client()
        try:
            containers = await asyncio.to_thread(client.containers.list, all=True)
        except (DockerException, APIError):
            return "unknown"
        network = str(get_config().get("preview_network_name"))
        found = False
        for container in containers:
            entry = _networks_of(container).get(network) or {}
            names = {str(getattr(container, "name", ""))}
            names |= {str(alias) for alias in (entry.get("Aliases") or [])}
            if network not in _networks_of(container) or host not in names:
                continue
            found = True
            if str(getattr(container, "status", "")) == "running":
                return "running"
        return "stopped" if found else "absent"

    # -- egress network (phase-39) -----------------------------------------------------

    async def ensure_egress_network(self) -> Any:
        """Get-or-create the routed network live validation borrows."""
        client = self._client()
        name = str(get_config().get("egress_network_name"))
        try:
            return await asyncio.to_thread(client.networks.get, name)
        except NotFound:
            kwargs = build_egress_network_kwargs(name)
            try:
                return await asyncio.to_thread(client.networks.create, **kwargs)
            except APIError as exc:
                raise SystemError("Failed to create the egress network") from exc

    async def attach_egress_network(self, project_id: str) -> bool:
        """Give the sandbox a route out for the duration of one live run.

        The caller **must** pair this with :meth:`detach_egress_network` in a ``finally``.
        """
        container = await self._get_container(project_id)
        if container is None:
            return False
        network = await self.ensure_egress_network()
        try:
            await asyncio.to_thread(network.connect, container.id)
        except APIError:
            logger.debug("sandbox %s already has egress", project_id, exc_info=True)
        return True

    async def detach_egress_network(self, project_id: str) -> None:
        """Take the route back out — the sandbox returns to its no-network default."""
        container = await self._get_container(project_id)
        if container is None:
            return
        client = self._client()
        name = str(get_config().get("egress_network_name"))
        try:
            network = await asyncio.to_thread(client.networks.get, name)
        except NotFound:
            return
        try:
            await asyncio.to_thread(network.disconnect, container.id, force=True)
        except APIError:
            logger.debug("sandbox %s had no egress to drop", project_id, exc_info=True)

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
        image = str(get_config().get("sandbox_image"))
        container = await self._get_container(project_id)
        created = False
        if container is not None and await self._is_stale(container, image):
            # Replace it, keeping the volumes: the workspace (and its git history) is preserved.
            logger.info("sandbox %s predates the current runtime spec; recreating", project_id)
            await asyncio.to_thread(container.remove, force=True)
            container = None
        if container is None:
            container = await self._create_container(project_id)
            created = True
        else:
            await asyncio.to_thread(container.reload)
            if container.status != "running":
                try:
                    await asyncio.to_thread(container.start)
                except (APIError, DockerException) as exc:
                    # A container whose network no longer exists can never start again — every
                    # preview/exec for the project would fail from here on. Its state lives on the
                    # volumes, so replacing it is the repair, not a loss.
                    if not _is_missing_network(exc):
                        raise SystemError("Failed to start the sandbox container") from exc
                    logger.info(
                        "sandbox %s cannot start (its network is gone); recreating", project_id
                    )
                    await asyncio.to_thread(container.remove, force=True)
                    container = await self._create_container(project_id)
                    created = True

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
            image=image,
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
        # The project's own network goes with its container — nothing else is ever on it, and
        # docker's default address pools are finite.
        await self._remove_network(project_id)
        if remove_volume:
            await self._remove_volumes(project_id)
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

    async def _remove_volumes(self, project_id: str) -> None:
        client = self._client()
        for name in (volume_name(project_id), home_volume_name(project_id)):
            try:
                volume = await asyncio.to_thread(client.volumes.get, name)
            except NotFound:
                continue
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

    async def prune_orphan_networks(self) -> int:
        """Remove per-project networks no container is attached to. Returns how many went.

        Docker's default address pools are finite (~30 networks), so orphans left by crashes or
        force-removed containers would eventually make new sandboxes unschedulable.

        A network is an orphan only when **no container references it at all**, and that has to be
        decided from the containers rather than from docker's refusal to remove: docker guards only
        networks with *live* endpoints, so it will happily delete the network a **stopped** sandbox
        is configured to use. Doing that leaves the container unstartable forever:

            /containers/<id>/start: Not Found
            ("failed to set up container networking: network <id> not found")
        """
        client = self._client()
        try:
            networks = await asyncio.to_thread(
                client.networks.list, filters={"label": f"{LABEL_ROLE}=sandbox"}
            )
            containers = await asyncio.to_thread(
                client.containers.list, all=True, filters={"label": f"{LABEL_MANAGED}=true"}
            )
        except (DockerException, APIError):
            logger.debug("could not list sandbox networks", exc_info=True)
            return 0

        referenced: set[str] = set()
        for container in containers:  # `all=True`: a stopped sandbox still needs its network
            attrs = getattr(container, "attrs", None) or {}
            mode = str((attrs.get("HostConfig") or {}).get("NetworkMode") or "")
            if mode:
                referenced.add(mode)
            referenced |= set((attrs.get("NetworkSettings") or {}).get("Networks") or {})

        pruned = 0
        for network in networks:
            if network.name in referenced:
                continue
            try:
                await asyncio.to_thread(network.remove)
            except (DockerException, APIError):
                continue  # in use after all (a race with a starting sandbox) — leave it
            pruned += 1
        if pruned:
            logger.info("pruned %d orphaned sandbox network(s)", pruned)
        return pruned

    async def reconcile(self) -> None:
        """Startup reconciliation: adopt managed containers, clear stale mappings, reap orphans."""
        client = self._client()
        containers = await asyncio.to_thread(
            client.containers.list, all=True, filters={"label": f"{LABEL_MANAGED}=true"}
        )
        by_project: dict[str, Any] = {}
        for container in containers:
            pid = container.labels.get(LABEL_PROJECT)
            if pid:
                by_project[pid] = container

        live_projects = {str(p.id) for p in await self._projects.all()}
        # A container whose project is gone (deleted project, a crashed run, a test database that
        # has since been dropped) can never be reached again, yet it keeps holding a subnet from
        # docker's finite address pools — which eventually blocks *new* sandboxes. Its volumes are
        # left alone: destroying data is an explicit action, never a side effect of startup.
        #
        # Never reap when the project list is empty: "no projects" and "pointed at the wrong (or a
        # not-yet-migrated) meta DB" look identical from here, and the second must not wipe every
        # sandbox on the host.
        for pid, container in list(by_project.items()) if live_projects else []:
            if pid in live_projects:
                continue
            try:
                await asyncio.to_thread(container.remove, force=True)
            except (DockerException, APIError):
                logger.debug("could not reap orphaned sandbox %s", pid, exc_info=True)
                continue
            by_project.pop(pid, None)
            self._activity.pop(pid, None)
            logger.info("reaped orphaned sandbox container for missing project %s", pid)

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

        # Networks whose container died with it would otherwise accumulate against docker's finite
        # address pools. Best-effort: never let cleanup break startup.
        try:
            await self.prune_orphan_networks()
        except Exception:
            logger.debug("orphan network prune failed", exc_info=True)


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
