"""Exec streaming: stdout/stderr chunks, exit codes, and timeout enforcement.

Driven through a real subprocess via LocalRuntime, so streaming/exit/timeout behaviour is genuinely
exercised (the Docker backend runs the identical service logic against the container).
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest
from beanie import PydanticObjectId

from app.core.config import get_config
from app.core.errors import UserError
from app.db.models import Project
from app.projects.service import ProjectService
from app.realtime.hub import get_hub
from app.sandbox.exec import EXIT_TIMEOUT, ExecService
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


def _py(code: str) -> list[str]:
    return [sys.executable, "-c", code]


async def test_streams_stdout_and_returns_exit_code(service: ExecService) -> None:
    project = await _project()
    outcome = await service.run(project, _py("print('hello world')"), capture=False)

    assert outcome.exit_code == 0
    assert outcome.timed_out is False
    assert outcome.cancelled is False
    assert outcome.duration_s >= 0

    events = get_hub().replay(str(project.id), 0)
    chunks = [e.payload["chunk"] for e in events if e.event == "terminal.output"]
    assert "hello world\n" in "".join(chunks)


async def test_streams_stderr_separately(service: ExecService) -> None:
    project = await _project()
    await service.run(
        project,
        _py("import sys; sys.stdout.write('out'); sys.stderr.write('err')"),
        capture=False,
    )

    events = [e for e in get_hub().replay(str(project.id), 0) if e.event == "terminal.output"]
    by_stream: dict[str, str] = {}
    for event in events:
        by_stream[event.payload["stream"]] = by_stream.get(event.payload["stream"], "") + str(
            event.payload["chunk"]
        )
    assert by_stream["stdout"] == "out"
    assert by_stream["stderr"] == "err"


async def test_nonzero_exit_code_is_reported(service: ExecService) -> None:
    outcome = await service.run(await _project(), _py("import sys; sys.exit(3)"), capture=False)
    assert outcome.exit_code == 3


async def test_emits_started_and_exited_events(service: ExecService) -> None:
    project = await _project()
    outcome = await service.run(project, _py("pass"), capture=False)

    statuses = [e for e in get_hub().replay(str(project.id), 0) if e.event == "exec.status"]
    assert statuses[0].payload["status"] == "started"
    assert statuses[0].payload["exec_id"] == outcome.exec_id
    assert statuses[-1].payload["status"] == "exited"
    assert statuses[-1].payload["exit_code"] == 0


async def test_output_streams_live_not_only_at_exit(service: ExecService) -> None:
    """Chunks must arrive while the process is still running, not be buffered to the end."""
    project = await _project()
    code = (
        "import sys, time\n"
        "sys.stdout.write('first\\n'); sys.stdout.flush()\n"
        "time.sleep(0.4)\n"
        "sys.stdout.write('second\\n'); sys.stdout.flush()\n"
    )
    outcome = await service.run(project, _py(code), capture=False)
    assert outcome.exit_code == 0

    events = [e for e in get_hub().replay(str(project.id), 0) if e.event == "terminal.output"]
    # Separate emissions prove the chunks were pushed as produced, not concatenated at exit.
    assert len(events) >= 2
    assert "first" in str(events[0].payload["chunk"])


async def test_timeout_kills_and_reports(service: ExecService) -> None:
    outcome = await service.run(
        await _project(), _py("import time; time.sleep(30)"), timeout=0.3, capture=False
    )
    assert outcome.timed_out is True
    assert outcome.exit_code == EXIT_TIMEOUT
    assert outcome.duration_s < 10  # returned promptly rather than waiting out the sleep


async def test_empty_command_rejected(service: ExecService) -> None:
    with pytest.raises(UserError):
        await service.run(await _project(), [], capture=False)


async def test_concurrency_cap_enforced(service: ExecService) -> None:
    project = await _project()
    # Occupy the cap with slow commands, then assert the next one is rejected.
    limit = int(get_config().get("sandbox_max_concurrent_execs"))
    running = [
        asyncio.create_task(
            service.run(project, _py("import time; time.sleep(1.5)"), capture=False)
        )
        for _ in range(limit)
    ]
    try:
        for _ in range(50):  # let them register
            await asyncio.sleep(0.02)
            if service.running_count(str(project.id)) >= limit:
                break
        with pytest.raises(UserError, match="concurrent"):
            await service.run(project, _py("pass"), capture=False)
    finally:
        for task in running:
            task.cancel()
        await asyncio.gather(*running, return_exceptions=True)
