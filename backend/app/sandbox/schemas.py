"""Sandbox API/return contracts (phase-11).

``SandboxState`` is a runtime view of the container; it is **not** persisted (only the
opaque ``container_id`` lives on ``Project.sandbox_id``), so it stays out of the domain
enum module.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class SandboxState(StrEnum):
    absent = "absent"  # no container exists for this project
    starting = "starting"  # created/restarting, not yet running
    running = "running"
    stopped = "stopped"  # exited/paused/dead — exists but not serving
    error = "error"  # docker reports an unexpected state


class SandboxInfo(BaseModel):
    project_id: str
    status: SandboxState
    container_id: str | None = None
    container_name: str | None = None
    image: str | None = None
    volume: str | None = None


class SandboxDestroyRequest(BaseModel):
    # Removing the volume deletes the project's workspace code — irreversible, so it is
    # opt-in and defaults off (see the phase rollback notes).
    remove_volume: bool = False
