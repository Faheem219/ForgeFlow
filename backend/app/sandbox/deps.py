"""Dependency bootstrap for a generated workspace.

The skeleton (phase-22) is copied into ``/workspace`` as *source only* — no ``node_modules``. Until
those exist nothing in the generated app can run: ``pnpm dev`` has no vite, ``pnpm exec vitest`` has
no vitest. Leaving it to the model is unreliable (it reads ``package.json``, concludes the deps are
"already installed", and starts a preview that cannot boot), so it happens deterministically here:

- **Idempotent** — probes the workspace's ``node_modules`` markers, so an already-installed
  workspace is a no-op and this can be called on every preview start.
- **Streamed** — the install runs through :class:`~app.sandbox.exec.ExecService`, so its output
  reaches the user as ``terminal.output``; a cold install takes minutes and silence looks like a
  hang. It also gets the longer ``sandbox_install_timeout_s`` wall clock.
- **Networked only here** — ``ExecService`` opens the bounded registry-egress window for install
  commands (:mod:`app.sandbox.network`); the sandbox has no network otherwise.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Protocol

from app.core.config import get_config
from app.db.models import Project
from app.realtime.hub import emit
from app.realtime.schemas import EventType
from app.sandbox.exec import ExecOutcome
from app.sandbox.runtime import WorkspaceRuntime

logger = logging.getLogger(__name__)

#: The install command. ``--prefer-offline`` reuses the (persistent, per-project) pnpm store, so a
#: re-install after a container restart mostly skips the network even though it has it.
INSTALL_CMD = ["pnpm", "install", "--prefer-offline"]

#: Root marker pnpm itself manages; the package dirs (from config, like preview uses) are where the
#: FE/BE actually resolve their imports from. All three must exist or the install is incomplete.
_ROOT_MARKER = "node_modules/.modules.yaml"


def _markers() -> tuple[str, ...]:
    config = get_config()
    packages = (str(config.get("preview_fe_dir")), str(config.get("preview_be_dir")))
    return (_ROOT_MARKER, *(f"{pkg}/node_modules" for pkg in packages if pkg))


#: Without this there is no workspace to install (the skeleton was never instantiated).
_SENTINEL = "package.json"


class _ExecRunner(Protocol):
    """The slice of :class:`~app.sandbox.exec.ExecService` used here (tests inject a fake)."""

    async def run(
        self,
        project: Project,
        cmd: list[str],
        cwd: str = "",
        env: dict[str, str] | None = None,
        timeout: float | None = None,
        capture: bool = True,
    ) -> ExecOutcome: ...


async def dependencies_installed(runtime: WorkspaceRuntime) -> bool:
    """True when every ``node_modules`` marker is present in the workspace."""
    for marker in _markers():
        if not await _exists(runtime, marker):
            return False
    return True


async def ensure_dependencies(
    project: Project,
    runtime: WorkspaceRuntime,
    *,
    exec_service: _ExecRunner | None = None,
    force: bool = False,
) -> ExecOutcome | None:
    """Install the workspace's dependencies when they are missing.

    Returns the install's :class:`ExecOutcome` when one actually ran, or ``None`` when it was
    skipped (no skeleton yet, or the deps are already present). Never raises for a *failed* install:
    it is reported (progress event + log) and the outcome is handed back so the caller can classify
    it — an install failure surfaced here is an *environment* problem (registry unreachable, disk
    full), which must not later resurface as a *test* failure blamed on generated code (phase-55).
    """
    project_id = str(project.id)
    if not await _exists(runtime, _SENTINEL):
        return None  # no skeleton yet — nothing to install
    if not force and await dependencies_installed(runtime):
        return None

    service = exec_service
    if service is None:
        from app.sandbox.exec import get_exec_service

        service = get_exec_service()

    await _progress(project_id, "started")
    outcome = await service.run(
        project,
        list(INSTALL_CMD),
        timeout=float(get_config().get("sandbox_install_timeout_s")),
    )
    ok = outcome.exit_code == 0
    if not ok:
        logger.warning(
            "dependency install failed for %s (exit=%s, timed_out=%s)",
            project_id,
            outcome.exit_code,
            outcome.timed_out,
        )
    await _progress(project_id, "done" if ok else "failed", exit_code=outcome.exit_code)
    return outcome


async def _progress(project_id: str, status: str, **extra: object) -> None:
    await emit(
        project_id,
        EventType.progress,
        {
            "step": "install_deps",
            "label": "Installing dependencies",
            "status": status,
            **extra,
        },
    )


async def _exists(runtime: WorkspaceRuntime, rel: str) -> bool:
    try:
        return bool(await asyncio.to_thread(runtime.exists, rel))
    except Exception:  # a probe must never be the thing that breaks a preview
        logger.debug("dependency probe failed for %s", rel, exc_info=True)
        return False
