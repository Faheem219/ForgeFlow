"""Timeouts + cancellation (phase-48): long ops are bounded and free their resources.

Exec is the representative long op — the agent loop, tests, deploy and design calls are all bounded
the same way (``asyncio.timeout`` + provider/HTTP timeouts). A hung command must not pin the event
loop or leak a process: a timeout kills it and reports 124; an explicit cancel kills it and marks it
cancelled. Driven through an in-memory runtime so the path is deterministic and cross-platform.
"""

from __future__ import annotations

import asyncio
from typing import cast

import pytest
from beanie import PydanticObjectId

from app.db.models import Project
from app.sandbox.exec import EXIT_TIMEOUT, ExecService
from app.sandbox.runtime import WorkspaceRuntime
from tests.resilience.conftest import HangingRuntime

pytestmark = pytest.mark.usefixtures("mongo_db")


async def _project() -> Project:
    return await Project(user_id=PydanticObjectId(), name="p", app_db_name="db").insert()


def _service(runtime: HangingRuntime) -> ExecService:
    async def provider(_project: Project) -> WorkspaceRuntime:
        # HangingRuntime implements only the exec slice of the runtime — all this path uses.
        return cast(WorkspaceRuntime, runtime)

    return ExecService(provider=provider)


async def test_a_hung_command_is_killed_at_the_timeout() -> None:
    runtime = HangingRuntime()
    service = _service(runtime)
    project = await _project()

    outcome = await service.run(project, ["sleep", "forever"], timeout=0.2, capture=False)

    assert outcome.timed_out is True
    assert outcome.exit_code == EXIT_TIMEOUT
    assert runtime.handles[0].killed is True  # the process was actually terminated


async def test_the_timeout_frees_the_running_slot() -> None:
    """A timed-out exec must not keep occupying the per-project concurrency budget."""
    runtime = HangingRuntime()
    service = _service(runtime)
    project = await _project()

    await service.run(project, ["sleep", "forever"], timeout=0.2, capture=False)

    assert service.running_count(str(project.id)) == 0


async def test_an_explicit_cancel_kills_the_command_and_reports_it() -> None:
    runtime = HangingRuntime()
    service = _service(runtime)
    project = await _project()
    pid = str(project.id)

    run_task = asyncio.create_task(
        service.run(project, ["sleep", "forever"], timeout=30, capture=False)
    )

    # Wait until the exec has registered, then cancel it by id.
    for _ in range(200):
        if service.running_count(pid) == 1:
            break
        await asyncio.sleep(0.01)
    assert service.running_count(pid) == 1

    [exec_id] = list(service._running[pid])
    await service.cancel(pid, exec_id)

    outcome = await asyncio.wait_for(run_task, timeout=5)
    assert outcome.cancelled is True
    assert runtime.handles[0].killed is True
    assert service.running_count(pid) == 0  # slot released


async def test_cancelling_an_unknown_exec_raises_not_found() -> None:
    from app.core.errors import NotFoundError

    service = _service(HangingRuntime())
    project = await _project()

    with pytest.raises(NotFoundError):
        await service.cancel(str(project.id), "no-such-exec")


async def test_concurrent_execs_are_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    """The cap is itself a resilience control — a runaway loop can't spawn unbounded processes."""
    from app.core.config import reset_config
    from app.core.errors import UserError

    monkeypatch.setenv("SANDBOX_MAX_CONCURRENT_EXECS", "1")
    reset_config()

    runtime = HangingRuntime()
    service = _service(runtime)
    project = await _project()

    first = asyncio.create_task(
        service.run(project, ["sleep", "forever"], timeout=30, capture=False)
    )
    for _ in range(200):
        if service.running_count(str(project.id)) == 1:
            break
        await asyncio.sleep(0.01)

    with pytest.raises(UserError, match="concurrent"):
        await service.run(project, ["echo", "hi"], timeout=5, capture=False)

    # Cleanup: release the first exec.
    runtime.handles[0].kill()
    await asyncio.wait_for(first, timeout=5)
