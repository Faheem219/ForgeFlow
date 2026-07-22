"""Container-crash recovery (phase-48): a dead sandbox is re-created from its volume.

The workspace lives on a named Docker volume that outlives any single container (phase-11). So a
crash is survivable: ``ensure`` re-creates the container, the volume re-attaches, and the generated
code is exactly where it was. These tests kill the container different ways and assert recovery
without touching the volume — all via the fake Docker client, so no daemon is needed.
"""

from __future__ import annotations

import pytest
from beanie import PydanticObjectId

from app.db.models import Project
from app.realtime.hub import get_hub
from app.realtime.schemas import EventType
from app.sandbox.manager import SandboxManager, container_name, volume_name
from tests.sandbox.fakes import FakeDockerClient

pytestmark = pytest.mark.usefixtures("mongo_db")


async def _project() -> Project:
    return await Project(user_id=PydanticObjectId(), name="app", app_db_name="db").insert()


def _volume_exists(client: FakeDockerClient, project_id: str) -> bool:
    return volume_name(project_id) in client.volumes._store


async def test_ensure_creates_the_container_and_its_volume() -> None:
    client = FakeDockerClient()
    manager = SandboxManager(client=client)
    project = await _project()

    await manager.ensure(project)

    assert container_name(str(project.id)) in client.containers._store
    assert _volume_exists(client, str(project.id))


async def test_a_removed_container_is_recreated_without_losing_the_volume() -> None:
    """The crash case: the container is gone, but the workspace volume must survive."""
    client = FakeDockerClient()
    manager = SandboxManager(client=client)
    project = await _project()
    pid = str(project.id)

    first = await manager.ensure(project)

    # Simulate a crash-and-reap: the container object disappears entirely.
    client.containers.get(container_name(pid)).remove()
    assert container_name(pid) not in client.containers._store
    assert _volume_exists(client, pid)  # the volume outlives the container

    second = await manager.ensure(project)

    assert container_name(pid) in client.containers._store
    assert second.container_id != first.container_id  # genuinely a new container
    assert _volume_exists(client, pid)  # ...attached to the same, untouched volume


async def test_recreation_reuses_the_existing_volume_rather_than_replacing_it() -> None:
    client = FakeDockerClient()
    manager = SandboxManager(client=client)
    project = await _project()
    pid = str(project.id)

    await manager.ensure(project)
    original_volume = client.volumes.get(volume_name(pid))
    client.containers.get(container_name(pid)).remove()

    await manager.ensure(project)

    # Same volume object — the workspace (and its git history for repair) is intact.
    assert client.volumes.get(volume_name(pid)) is original_volume


async def test_a_stopped_container_is_restarted_not_recreated() -> None:
    client = FakeDockerClient()
    manager = SandboxManager(client=client)
    project = await _project()
    pid = str(project.id)

    first = await manager.ensure(project)
    container = client.containers.get(container_name(pid))
    container.stop()  # exited, but not removed

    second = await manager.ensure(project)

    assert second.container_id == first.container_id  # same container, restarted
    assert container.start_calls >= 1
    assert container.status == "running"


async def test_recovery_updates_the_persisted_sandbox_pointer() -> None:
    client = FakeDockerClient()
    manager = SandboxManager(client=client)
    project = await _project()
    pid = str(project.id)

    await manager.ensure(project)
    client.containers.get(container_name(pid)).remove()
    await manager.ensure(project)

    # The Project row now points at the live container, not the dead one.
    reloaded = await Project.get(project.id)
    assert reloaded is not None
    assert reloaded.sandbox_id == client.containers.get(container_name(pid)).id


async def test_recovery_notifies_over_the_realtime_channel() -> None:
    """The user must learn their sandbox came back (event carries created=True)."""
    client = FakeDockerClient()
    manager = SandboxManager(client=client)
    project = await _project()
    pid = str(project.id)

    await manager.ensure(project)
    get_hub().reset()  # forget the first-create event; watch only the recovery
    client.containers.get(container_name(pid)).remove()

    await manager.ensure(project)

    events = get_hub().replay(pid, 0)
    sandbox_events = [e for e in events if e.event == EventType.sandbox_status]
    assert sandbox_events, "recovery should emit a sandbox_status event"
    assert sandbox_events[-1].payload.get("created") is True
