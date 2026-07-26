"""Preview networking must stay host-isolated, and URLs must resolve per environment.

The network spec is asserted purely (no daemon), mirroring how phase-11's isolation flags are
covered — this is the guarantee that the phase-15 relaxation of `network_mode=none` is minimal.
"""

from __future__ import annotations

import pytest

from app.core.config import get_config
from app.sandbox.manager import (
    LABEL_MANAGED,
    LABEL_ROLE,
    SandboxManager,
    build_preview_network_kwargs,
    container_name,
)
from app.sandbox.preview import preview_urls
from tests.sandbox.fakes import FakeDockerClient

LABELS = {"forgeflow.managed": "true", "forgeflow.project": "p1"}


def test_preview_network_is_internal_and_labelled() -> None:
    kwargs = build_preview_network_kwargs("forgeflow-preview")

    # The isolation guarantee: no route to the host or the internet.
    assert kwargs["internal"] is True
    assert kwargs["driver"] == "bridge"
    assert kwargs["name"] == "forgeflow-preview"
    assert kwargs["labels"][LABEL_MANAGED] == "true"
    assert kwargs["labels"][LABEL_ROLE] == "preview"
    # Nothing here may grant host networking.
    assert "network_mode" not in kwargs
    assert kwargs.get("driver") != "host"


def test_sandbox_default_still_has_no_network() -> None:
    """Preview must not weaken the phase-11 default posture for ordinary sandboxes."""
    from app.sandbox.manager import (
        build_run_kwargs,
        build_sandbox_network_kwargs,
        sandbox_network_name,
    )

    kwargs = build_run_kwargs(
        "p1", image="img", cpu_limit=1.0, mem_limit="1g", pids_limit=256, read_only=True
    )
    # The default is the project's *own* sealed network — reachable by nothing, routing nowhere.
    assert kwargs["network_mode"] == sandbox_network_name("p1")
    assert build_sandbox_network_kwargs(sandbox_network_name("p1"))["internal"] is True


async def test_attach_creates_the_network_then_connects() -> None:
    client = FakeDockerClient()
    client.containers.seed(container_name("p1"), LABELS, status="running")
    manager = SandboxManager(client=client)

    attached = await manager.attach_preview_network("p1")
    assert attached is True

    name = str(get_config().get("preview_network_name"))
    network = client.networks._store[name]
    assert network.kwargs["internal"] is True
    assert container_name("p1") in {c.name for c in network.connected}


async def test_attach_reuses_an_existing_network() -> None:
    client = FakeDockerClient()
    client.containers.seed(container_name("p1"), LABELS, status="running")
    client.containers.seed(container_name("p2"), {**LABELS, "forgeflow.project": "p2"})
    manager = SandboxManager(client=client)

    await manager.attach_preview_network("p1")
    await manager.attach_preview_network("p2")

    assert len(client.networks._store) == 1  # created once, joined twice
    assert len(client.network_create_calls) == 1


async def test_attach_without_a_container_is_a_noop() -> None:
    manager = SandboxManager(client=FakeDockerClient())
    assert await manager.attach_preview_network("missing") is False


async def test_detach_removes_the_sandbox_from_the_network() -> None:
    client = FakeDockerClient()
    client.containers.seed(container_name("p1"), LABELS, status="running")
    manager = SandboxManager(client=client)

    await manager.attach_preview_network("p1")
    await manager.detach_preview_network("p1")

    name = str(get_config().get("preview_network_name"))
    assert client.networks._store[name].connected == []


async def test_detach_is_safe_when_nothing_is_attached() -> None:
    client = FakeDockerClient()
    client.containers.seed(container_name("p1"), LABELS, status="running")
    manager = SandboxManager(client=client)
    await manager.detach_preview_network("p1")  # no network yet — must not raise


def test_preview_urls_local_default() -> None:
    fe, be = preview_urls("abc123")
    assert fe == "http://abc123.preview.localhost"
    assert be == "http://abc123.api.preview.localhost"


def test_preview_urls_use_the_prod_wildcard_domain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PREVIEW_BASE_DOMAIN", "preview.forgeflow.dev")
    from app.core.config import reset_config

    reset_config()

    fe, be = preview_urls("abc123")
    assert fe == "https://abc123.preview.forgeflow.dev"
    assert be == "https://abc123.api.preview.forgeflow.dev"


def test_preview_urls_are_project_scoped() -> None:
    assert preview_urls("a")[0] != preview_urls("b")[0]
