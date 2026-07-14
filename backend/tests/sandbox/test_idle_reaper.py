"""Idle reaping: containers idle beyond the timeout are stopped; activity resets the timer.

Time is injected (monotonic ``now``) so the sweep is deterministic and instant.
"""

from __future__ import annotations

import pytest

from app.sandbox.manager import SandboxManager, container_name
from tests.sandbox.fakes import FakeDockerClient

LABELS = {"forgeflow.managed": "true", "forgeflow.project": "p1"}


@pytest.fixture
def manager_with_running_container() -> tuple[SandboxManager, FakeDockerClient]:
    client = FakeDockerClient()
    client.containers.seed(container_name("p1"), LABELS, status="running")
    return SandboxManager(client=client), client


async def test_idle_container_is_reaped(
    manager_with_running_container: tuple[SandboxManager, FakeDockerClient],
) -> None:
    manager, client = manager_with_running_container
    manager.touch("p1", now=1000.0)

    # Not yet idle.
    reaped = await manager.reap_idle_once(now=1005.0, idle_timeout=10)
    assert reaped == []
    assert client.containers.get(container_name("p1")).status == "running"
    assert "p1" in manager._activity

    # Past the timeout → stopped and de-registered.
    reaped = await manager.reap_idle_once(now=1020.0, idle_timeout=10)
    assert reaped == ["p1"]
    assert client.containers.get(container_name("p1")).status == "exited"
    assert "p1" not in manager._activity


async def test_activity_resets_the_timer(
    manager_with_running_container: tuple[SandboxManager, FakeDockerClient],
) -> None:
    manager, client = manager_with_running_container

    manager.touch("p1", now=0.0)
    assert await manager.reap_idle_once(now=5.0, idle_timeout=10) == []

    # Fresh activity moves the deadline forward.
    manager.touch("p1", now=8.0)
    assert await manager.reap_idle_once(now=15.0, idle_timeout=10) == []  # 15-8 < 10
    assert client.containers.get(container_name("p1")).status == "running"

    # Eventually idle relative to the *latest* touch.
    assert await manager.reap_idle_once(now=20.0, idle_timeout=10) == ["p1"]  # 20-8 >= 10


async def test_reaper_emits_sandbox_status(
    manager_with_running_container: tuple[SandboxManager, FakeDockerClient],
) -> None:
    from app.realtime.hub import get_hub

    manager, _ = manager_with_running_container
    manager.touch("p1", now=0.0)
    await manager.reap_idle_once(now=100.0, idle_timeout=10)

    events = get_hub().replay("p1", 0)
    assert [e.event for e in events] == ["sandbox.status"]
    assert events[0].payload["reason"] == "idle"


async def test_empty_activity_makes_no_docker_calls() -> None:
    # A fresh instance with nothing touched must not touch docker (keeps a daemon-less dev
    # box quiet). No client is provided; any docker access would raise.
    manager = SandboxManager()
    assert await manager.reap_idle_once(now=10_000.0, idle_timeout=10) == []
