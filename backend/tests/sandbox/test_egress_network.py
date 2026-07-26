"""The live-validation egress window (phase-39) must stay narrow.

Live validation is the one thing in ForgeFlow that needs the sandbox to reach the public internet:
Playwright has to load the *deployed* URL. That makes this the widest relaxation of the
``network_mode=none`` default, so the guarantees are asserted here rather than assumed — the route
is a bridge (never the host namespace), it is labelled for auditing, and the default posture for an
ordinary sandbox is unchanged. That it is also narrow in *time* (attached per run, always detached)
is covered in ``tests/testing/test_live_runner.py``.
"""

from __future__ import annotations

from app.core.config import get_config
from app.sandbox.manager import (
    LABEL_MANAGED,
    LABEL_ROLE,
    SandboxManager,
    build_egress_network_kwargs,
    build_preview_network_kwargs,
    build_run_kwargs,
    build_sandbox_network_kwargs,
    container_name,
    sandbox_network_name,
)
from tests.sandbox.fakes import FakeDockerClient

LABELS = {"forgeflow.managed": "true", "forgeflow.project": "p1"}


def test_egress_network_routes_out_but_is_never_the_host() -> None:
    kwargs = build_egress_network_kwargs("forgeflow-egress")

    # Routed on purpose — a deployed URL is on the internet and cannot be reached otherwise.
    assert kwargs["internal"] is False
    # …but still a bridge: generated code gets its own namespace, never the host's.
    assert kwargs["driver"] == "bridge"
    assert "network_mode" not in kwargs
    assert kwargs["labels"][LABEL_MANAGED] == "true"
    assert kwargs["labels"][LABEL_ROLE] == "egress"


def test_egress_is_a_separate_network_from_preview() -> None:
    """Preview must stay sealed; the two relaxations may never collapse into one network."""
    egress = build_egress_network_kwargs(str(get_config().get("egress_network_name")))
    preview = build_preview_network_kwargs(str(get_config().get("preview_network_name")))

    assert egress["name"] != preview["name"]
    assert preview["internal"] is True and egress["internal"] is False


def test_the_sandbox_default_still_has_no_network() -> None:
    """Adding egress must not weaken the phase-11 posture for an ordinary sandbox."""
    kwargs = build_run_kwargs(
        "p1", image="img", cpu_limit=1.0, mem_limit="1g", pids_limit=256, read_only=True
    )
    assert kwargs["network_mode"] == sandbox_network_name("p1")
    assert build_sandbox_network_kwargs(sandbox_network_name("p1"))["internal"] is True


async def test_attach_creates_the_routed_network_then_connects() -> None:
    client = FakeDockerClient()
    client.containers.seed(container_name("p1"), LABELS, status="running")
    manager = SandboxManager(client=client)

    assert await manager.attach_egress_network("p1") is True

    name = str(get_config().get("egress_network_name"))
    network = client.networks._store[name]
    assert network.kwargs["internal"] is False
    assert container_name("p1") in {c.name for c in network.connected}


async def test_detach_returns_the_sandbox_to_no_network() -> None:
    client = FakeDockerClient()
    client.containers.seed(container_name("p1"), LABELS, status="running")
    manager = SandboxManager(client=client)

    await manager.attach_egress_network("p1")
    await manager.detach_egress_network("p1")

    name = str(get_config().get("egress_network_name"))
    assert client.networks._store[name].connected == []


async def test_attach_without_a_container_is_a_noop() -> None:
    manager = SandboxManager(client=FakeDockerClient())
    assert await manager.attach_egress_network("missing") is False


async def test_detach_is_safe_when_no_egress_was_granted() -> None:
    client = FakeDockerClient()
    client.containers.seed(container_name("p1"), LABELS, status="running")
    manager = SandboxManager(client=client)
    await manager.detach_egress_network("p1")  # never attached — must not raise
