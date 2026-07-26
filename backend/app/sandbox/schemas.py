"""Sandbox API/return contracts (phase-11).

``SandboxState`` is a runtime view of the container; it is **not** persisted (only the
opaque ``container_id`` lives on ``Project.sandbox_id``), so it stays out of the domain
enum module.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


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


# --- Workspace filesystem + git (phase-12) ---


class FileNode(BaseModel):
    path: str
    type: Literal["file", "dir"]  # noqa: A003 - external contract field name
    size: int | None = None


class FileContent(BaseModel):
    path: str
    content: str
    size: int


class WriteFileRequest(BaseModel):
    path: str = Field(min_length=1)
    content: str


class MkdirRequest(BaseModel):
    path: str = Field(min_length=1)


class MoveRequest(BaseModel):
    src: str = Field(min_length=1)
    dst: str = Field(min_length=1)


class GitCommitRequest(BaseModel):
    message: str = Field(min_length=1, max_length=500)


class GitCommitResponse(BaseModel):
    sha: str | None
    committed: bool


class GitInfo(BaseModel):
    current_sha: str | None
    last_passing: str | None


# --- Live preview (phase-15) ---


class PreviewProcess(StrEnum):
    frontend = "frontend"
    backend = "backend"


class PreviewStatus(StrEnum):
    stopped = "stopped"
    starting = "starting"  # process is up but not answering health probes yet
    running = "running"
    failed = "failed"  # process exited on its own (crash / bad command)


class PreviewInfo(BaseModel):
    project_id: str
    fe_url: str | None = None
    be_url: str | None = None
    fe_status: PreviewStatus = PreviewStatus.stopped
    be_status: PreviewStatus = PreviewStatus.stopped
    #: Set when the dev servers are healthy but something outside the sandbox stops the URLs from
    #: working — today: the reverse proxy that serves ``*.preview.localhost`` is not running, so the
    #: browser gets ERR_CONNECTION_REFUSED while everything inside the sandbox is fine.
    warning: str | None = None
