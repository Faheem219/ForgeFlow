"""A preview whose URLs cannot possibly load must say so, not report a healthy "running".

`*.preview.localhost` is served by the Caddy proxy and by nothing else. Run the API on the host
without the compose stack (a normal way to develop here) and the dev servers come up perfectly
inside the sandbox while the browser gets `ERR_CONNECTION_REFUSED` — an error that points at the
generated app, which is the last place the problem actually is.
"""

from __future__ import annotations

import pytest
from beanie import PydanticObjectId

from app.core.config import get_config
from app.sandbox.manager import SandboxManager, container_name
from tests.sandbox.fakes import FakeDockerClient

pytestmark = pytest.mark.usefixtures("mongo_db")

LABELS = {"forgeflow.managed": "true", "forgeflow.project": "p1"}


def _network(client: FakeDockerClient, *attached: str) -> None:
    """Create the preview network with ``attached`` container names on it."""
    name = str(get_config().get("preview_network_name"))
    network = client.networks.create(name=name, labels={"forgeflow.role": "preview"})
    network.attrs = {"Containers": {f"c{i}": {"Name": n} for i, n in enumerate(attached)}}


async def test_no_proxy_on_the_network_is_reported_as_missing() -> None:
    client = FakeDockerClient()
    manager = SandboxManager(client=client)
    # Only the sandbox itself joined — exactly the state when the compose stack is not running.
    _network(client, container_name("p1"))

    assert await manager.preview_proxy_attached() is False


async def test_a_proxy_alongside_the_sandbox_is_detected() -> None:
    client = FakeDockerClient()
    manager = SandboxManager(client=client)
    _network(client, container_name("p1"), "forgeflow-proxy-1")

    assert await manager.preview_proxy_attached() is True


async def test_a_missing_network_is_not_a_proxy() -> None:
    manager = SandboxManager(client=FakeDockerClient())  # nothing created at all

    assert await manager.preview_proxy_attached() is False


async def test_two_sandboxes_are_still_not_a_proxy() -> None:
    """Detection is by exclusion, so it must not count another project's sandbox as the proxy."""
    client = FakeDockerClient()
    manager = SandboxManager(client=client)
    _network(client, container_name("p1"), container_name("p2"))

    assert await manager.preview_proxy_attached() is False


async def test_the_warning_names_the_command_that_fixes_it() -> None:
    from app.sandbox.preview import PROXY_MISSING_WARNING

    # The whole point is that the user can act on it without reading the source.
    assert "make proxy" in PROXY_MISSING_WARNING
    assert "refuse to connect" in PROXY_MISSING_WARNING


async def test_preview_info_carries_the_warning_through() -> None:
    """It has to survive as far as the API response — the UI renders this field."""
    from app.sandbox.schemas import PreviewInfo, PreviewStatus

    info = PreviewInfo(
        project_id=str(PydanticObjectId()),
        fe_status=PreviewStatus.running,
        be_status=PreviewStatus.running,
        warning="proxy is down",
    )

    assert info.model_dump(mode="json")["warning"] == "proxy is down"
    # …and stays absent for a healthy preview rather than becoming an empty string.
    assert PreviewInfo(project_id="p").model_dump(mode="json")["warning"] is None
