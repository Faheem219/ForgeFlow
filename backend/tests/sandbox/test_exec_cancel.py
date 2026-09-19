"""Cancellation must actually kill the process (and its children) and return promptly."""

from __future__ import annotations

import asyncio
import os
import sys

import pytest
from beanie import PydanticObjectId

from app.core.errors import NotFoundError
from app.db.models import Project
from app.projects.service import ProjectService
from app.realtime.hub import get_hub
from app.sandbox.exec import ExecService
from app.sandbox.runtime import LocalRuntime, WorkspaceRuntime
from tests.sandbox.conftest import requires_posix_runtime

pytestmark = [pytest.mark.usefixtures("mongo_db"), requires_posix_runtime]


async def _project() -> Project:
    return await ProjectService().create_project(PydanticObjectId(), "p")


@pytest.fixture
def service(tmp_path: object) -> ExecService:
    root = os.path.join(str(tmp_path), "ws")

    async def provider(_project: Project) -> WorkspaceRuntime:
        return LocalRuntime(root)

    return ExecService(provider=provider)


async def _wait_until_running(service: ExecService, project_id: str) -> str:
    for _ in range(100):
        execs = service._running.get(project_id, {})
        if execs:
            return next(iter(execs))
        await asyncio.sleep(0.02)
    raise AssertionError("command never registered as running")


async def test_cancel_kills_long_running_command(service: ExecService) -> None:
    project = await _project()
    task = asyncio.create_task(
        service.run(project, [sys.executable, "-c", "import time; time.sleep(60)"], capture=False)
    )

    exec_id = await _wait_until_running(service, str(project.id))
    await service.cancel(str(project.id), exec_id)

    # Must return promptly rather than waiting out the 60s sleep.
    outcome = await asyncio.wait_for(task, timeout=10)
    assert outcome.cancelled is True
    assert outcome.exec_id == exec_id
    assert outcome.exit_code != 0  # killed by signal
    assert outcome.duration_s < 10


async def test_cancel_frees_the_concurrency_slot(service: ExecService) -> None:
    project = await _project()
    task = asyncio.create_task(
        service.run(project, [sys.executable, "-c", "import time; time.sleep(60)"], capture=False)
    )
    exec_id = await _wait_until_running(service, str(project.id))
    assert service.running_count(str(project.id)) == 1

    await service.cancel(str(project.id), exec_id)
    await asyncio.wait_for(task, timeout=10)
    assert service.running_count(str(project.id)) == 0


def _pid_alive(pid: int) -> bool:
    """Running — not gone, and not a zombie (killed but not yet reaped by its adoptive parent,
    which on a CI runner can take a while; ``kill -0`` alone reports a zombie as alive)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as handle:
            return handle.read().rsplit(") ", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return True  # no /proc to consult: trust kill -0


async def _child_pid_from_output(project_id: str) -> int:
    """The command prints its child's PID on stdout; pull it off the event stream."""
    for _ in range(100):
        await asyncio.sleep(0.05)
        text = "".join(
            str(e.payload["chunk"])
            for e in get_hub().replay(project_id, 0)
            if e.event == "terminal.output"
        )
        if text.strip():
            return int(text.strip().splitlines()[0])
    raise AssertionError("child pid never reported")


async def test_cancel_kills_child_processes(service: ExecService) -> None:
    """Cancelling must take the whole process group — a spawned child must not be orphaned."""
    project = await _project()
    code = (
        "import subprocess, sys, time\n"
        "child = subprocess.Popen(['sleep', '60'])\n"
        "sys.stdout.write(str(child.pid) + '\\n'); sys.stdout.flush()\n"
        "time.sleep(60)\n"
    )
    task = asyncio.create_task(service.run(project, [sys.executable, "-c", code], capture=False))

    exec_id = await _wait_until_running(service, str(project.id))
    child_pid = await _child_pid_from_output(str(project.id))
    assert _pid_alive(child_pid)

    await service.cancel(str(project.id), exec_id)
    outcome = await asyncio.wait_for(task, timeout=10)
    assert outcome.cancelled is True

    for _ in range(60):  # allow a moment for the reparented child to be reaped
        if not _pid_alive(child_pid):
            break
        await asyncio.sleep(0.05)
    assert not _pid_alive(child_pid), "child process survived cancellation"


async def test_cancel_unknown_exec_is_not_found(service: ExecService) -> None:
    with pytest.raises(NotFoundError):
        await service.cancel(str((await _project()).id), "does-not-exist")
