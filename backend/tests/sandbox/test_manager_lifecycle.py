"""Ensure/start/stop/destroy lifecycle + sandbox_id persistence (Docker faked).

These exercise the manager end-to-end against a disposable Mongo (skips if unreachable) so the
``Project.sandbox_id`` persistence and the reload/adopt paths are covered. The isolation *spec*
is asserted separately (test_isolation_flags); here we assert it is actually applied through
``ensure``.
"""

from __future__ import annotations

import pytest
from beanie import PydanticObjectId

from app.db.models import Project
from app.projects.service import ProjectService
from app.sandbox.manager import (
    SandboxManager,
    container_name,
    sandbox_network_name,
    volume_name,
)
from app.sandbox.schemas import SandboxState
from tests.sandbox.fakes import FakeDockerClient

pytestmark = pytest.mark.usefixtures("mongo_db")


async def _make_project(name: str = "p") -> Project:
    return await ProjectService().create_project(PydanticObjectId(), name)


async def test_ensure_creates_starts_and_persists() -> None:
    project = await _make_project()
    client = FakeDockerClient()
    manager = SandboxManager(client=client)

    info = await manager.ensure(project)

    assert info.status == SandboxState.running
    assert info.container_id is not None
    assert info.container_name == container_name(str(project.id))

    # sandbox_id persisted on the project.
    reloaded = await Project.get(project.id)
    assert reloaded is not None
    assert reloaded.sandbox_id == info.container_id

    # A per-project volume was created and the isolation spec was applied through ensure.
    assert volume_name(str(project.id)) in client.volumes._store
    assert len(client.run_calls) == 1
    assert client.run_calls[0]["network_mode"] == sandbox_network_name(str(project.id))
    assert client.run_calls[0]["cap_drop"] == ["ALL"]
    assert client.run_calls[0]["pids_limit"] > 0


async def test_ensure_is_idempotent_when_running() -> None:
    project = await _make_project()
    client = FakeDockerClient()
    manager = SandboxManager(client=client)

    first = await manager.ensure(project)
    second = await manager.ensure(project)

    assert len(client.run_calls) == 1  # not recreated
    assert second.container_id == first.container_id


async def test_stop_then_ensure_restarts_same_container() -> None:
    project = await _make_project()
    client = FakeDockerClient()
    manager = SandboxManager(client=client)

    await manager.ensure(project)
    name = container_name(str(project.id))

    stopped = await manager.stop(project)
    assert stopped.status == SandboxState.stopped
    assert client.containers.get(name).status == "exited"

    restarted = await manager.ensure(project)
    assert restarted.status == SandboxState.running
    assert len(client.run_calls) == 1  # started, not recreated
    assert client.containers.get(name).start_calls == 1


async def test_destroy_removes_container_and_clears_mapping() -> None:
    project = await _make_project()
    client = FakeDockerClient()
    manager = SandboxManager(client=client)

    await manager.ensure(project)
    name = container_name(str(project.id))
    vol = volume_name(str(project.id))

    info = await manager.destroy(project, remove_volume=True)
    assert info.status == SandboxState.absent

    with pytest.raises(Exception):  # noqa: B017 - NotFound from the fake
        client.containers.get(name)
    assert vol not in client.volumes._store

    reloaded = await Project.get(project.id)
    assert reloaded is not None
    assert reloaded.sandbox_id is None


async def test_destroy_keeps_volume_by_default() -> None:
    project = await _make_project()
    client = FakeDockerClient()
    manager = SandboxManager(client=client)

    await manager.ensure(project)
    vol = volume_name(str(project.id))

    await manager.destroy(project)  # remove_volume defaults False
    assert vol in client.volumes._store  # workspace code preserved


async def test_two_projects_never_share_state() -> None:
    a = await _make_project("a")
    b = await _make_project("b")
    client = FakeDockerClient()
    manager = SandboxManager(client=client)

    info_a = await manager.ensure(a)
    info_b = await manager.ensure(b)

    assert info_a.container_name != info_b.container_name
    assert info_a.volume != info_b.volume
    assert info_a.container_id != info_b.container_id
    assert {container_name(str(a.id)), container_name(str(b.id))} <= set(
        client.containers._store.keys()
    )


async def test_status_reports_absent_before_ensure() -> None:
    project = await _make_project()
    manager = SandboxManager(client=FakeDockerClient())

    info = await manager.status(project)
    assert info.status == SandboxState.absent
    assert info.container_id is None


async def test_reconcile_adopts_and_clears() -> None:
    adopted = await _make_project("adopt")
    stale = await _make_project("stale")
    client = FakeDockerClient()

    # A live managed container exists for `adopted` with an id the project doesn't yet know.
    seeded = client.containers.seed(
        container_name(str(adopted.id)),
        {"forgeflow.managed": "true", "forgeflow.project": str(adopted.id)},
        status="running",
    )
    # `stale` points at a container that no longer exists.
    stale.sandbox_id = "ghost-id"
    await stale.save()

    manager = SandboxManager(client=client)
    await manager.reconcile()

    reloaded_adopted = await Project.get(adopted.id)
    reloaded_stale = await Project.get(stale.id)
    assert reloaded_adopted is not None and reloaded_adopted.sandbox_id == seeded.id
    assert reloaded_stale is not None and reloaded_stale.sandbox_id is None
    # A running adopted container is registered for idle reaping.
    assert str(adopted.id) in manager._activity
