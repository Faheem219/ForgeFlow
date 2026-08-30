"""A preview needs three containers, and only one of them comes back by itself.

Observed after a docker restart: the sandbox is running (the manager starts it on demand) while the
two containers beside it on the preview network — the Caddy proxy and the generated-app MongoDB —
are still exited, because a compose service with `restart: unless-stopped` is deliberately *not*
restarted when the daemon that stopped it comes back. The user sees two unrelated-looking failures,
neither of which names the restart: the preview URL refuses to connect, and the generated backend
logs `getaddrinfo EAI_AGAIN forgeflow-appdb`.

Both are the same missing step, so preview start takes it: restart what it finds stopped on the
preview network, and warn precisely about whatever is still not there.
"""

from __future__ import annotations

import pytest

from app.core.config import get_config, reset_config
from app.sandbox.manager import SandboxManager, container_name
from app.sandbox.preview import DB_HOST_UNRESOLVABLE_WARNING, docker_db_host
from tests.sandbox.fakes import FakeDockerClient

pytestmark = pytest.mark.usefixtures("mongo_db")

APPDB = "forgeflow-appdb"
PROXY = "forgeflow-proxy-1"


def _preview() -> str:
    return str(get_config().get("preview_network_name"))


def _seed(
    client: FakeDockerClient,
    name: str,
    *,
    status: str,
    on_preview: bool = True,
    aliases: list[str] | None = None,
    health: str = "",
) -> None:
    networks = {_preview(): aliases or []} if on_preview else {"bridge": []}
    client.containers.seed(name, labels={}, status=status, networks=networks, health=health)


# ---------------------------------------------------------------- restarting what stopped


async def test_the_stopped_proxy_and_database_are_both_restarted() -> None:
    client = FakeDockerClient()
    _seed(client, PROXY, status="exited")
    _seed(client, APPDB, status="exited")

    started = await SandboxManager(client=client).start_preview_dependencies()

    assert sorted(started) == [APPDB, PROXY]
    assert client.containers.get(PROXY).status == "running"
    assert client.containers.get(APPDB).status == "running"


async def test_a_running_dependency_is_left_alone() -> None:
    """Idempotent: preview start calls this every time, including when nothing is wrong."""
    client = FakeDockerClient()
    _seed(client, PROXY, status="running")

    assert await SandboxManager(client=client).start_preview_dependencies() == []
    assert client.containers.get(PROXY).start_calls == 0


async def test_a_stopped_sandbox_is_never_started_here() -> None:
    """Sandbox lifecycle belongs to `ensure` — starting one behind its back skips the staleness
    check that recreates a container predating the current image or mount spec."""
    client = FakeDockerClient()
    sandbox = container_name("p1")
    _seed(client, sandbox, status="exited")

    assert await SandboxManager(client=client).start_preview_dependencies() == []
    assert client.containers.get(sandbox).start_calls == 0


async def test_containers_off_the_preview_network_are_untouched() -> None:
    """The daemon is the developer's whole machine — only ForgeFlow's own neighbours are ours."""
    client = FakeDockerClient()
    _seed(client, "someone-elses-postgres", status="exited", on_preview=False)

    assert await SandboxManager(client=client).start_preview_dependencies() == []
    assert client.containers.get("someone-elses-postgres").start_calls == 0


async def test_one_failing_dependency_does_not_block_the_other() -> None:
    client = FakeDockerClient()
    _seed(client, PROXY, status="exited")
    _seed(client, APPDB, status="exited")
    client.containers.get(PROXY).fail_start_with = "driver failed programming external connectivity"

    started = await SandboxManager(client=client).start_preview_dependencies()

    assert started == [APPDB]  # the proxy failure is reported as a warning, not an exception


async def test_a_dependency_that_never_becomes_healthy_does_not_hang_the_preview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bounded, like every other wait here: an unhealthy mongo must not wedge preview start."""
    monkeypatch.setenv("PREVIEW_DEPS_READY_TIMEOUT_S", "0")
    reset_config()
    client = FakeDockerClient()
    _seed(client, APPDB, status="exited", health="starting")

    assert await SandboxManager(client=client).start_preview_dependencies() == [APPDB]


async def test_a_healthy_dependency_satisfies_the_wait() -> None:
    client = FakeDockerClient()
    _seed(client, APPDB, status="exited", health="healthy")

    assert await SandboxManager(client=client).start_preview_dependencies() == [APPDB]


# ---------------------------------------------------------------- diagnosing what is left


async def test_dns_state_tells_stopped_apart_from_absent() -> None:
    """Different fixes: one is recoverable from here, the other needs `make host-deps`."""
    client = FakeDockerClient()
    manager = SandboxManager(client=client)
    _seed(client, APPDB, status="exited")

    assert await manager.preview_dns_state(APPDB) == "stopped"
    assert await manager.preview_dns_state("never-created") == "absent"

    client.containers.get(APPDB).start()
    assert await manager.preview_dns_state(APPDB) == "running"


async def test_a_network_alias_resolves_too() -> None:
    """Compose publishes the service name as an alias, so `mongodb://appdb` is a valid address."""
    client = FakeDockerClient()
    _seed(client, "forgeflow-appdb-1", status="running", aliases=["appdb"])

    assert await SandboxManager(client=client).preview_dns_state("appdb") == "running"


async def test_a_container_on_another_network_does_not_answer_for_the_name() -> None:
    """It resolves for whoever shares *that* network — never for a sandbox on the preview one."""
    client = FakeDockerClient()
    _seed(client, APPDB, status="running", on_preview=False)

    assert await SandboxManager(client=client).preview_dns_state(APPDB) == "absent"


@pytest.mark.parametrize(
    ("uri", "expected"),
    [
        ("mongodb://forgeflow-appdb:27017/app", "forgeflow-appdb"),
        ("mongodb://appdb/app", "appdb"),
        # Routable addresses all carry a dot — never diagnose those as a missing container.
        ("mongodb+srv://cluster0.abcd.mongodb.net/app", None),
        ("mongodb://10.5.0.3:27017/app", None),
        # Loopback is a different diagnosis with a different fix (DB_UNREACHABLE_WARNING).
        ("mongodb://localhost:27017/app", None),
        ("mongodb://127.0.0.1:27017/app", None),
    ],
)
def test_only_a_bare_docker_name_is_diagnosed_this_way(uri: str, expected: str | None) -> None:
    assert docker_db_host(uri) == expected


def test_the_warning_survives_a_password_in_the_uri() -> None:
    host = docker_db_host("mongodb://admin:sup3rsecret@forgeflow-appdb:27017/app")

    assert host == "forgeflow-appdb"
    warning = DB_HOST_UNRESOLVABLE_WARNING.format(host=host)
    assert "sup3rsecret" not in warning and "admin" not in warning


def test_the_warning_names_the_symptom_and_the_command() -> None:
    """The user meets this as `EAI_AGAIN` in the app's own log; the warning has to bridge that."""
    warning = DB_HOST_UNRESOLVABLE_WARNING.format(host=APPDB)

    assert "EAI_AGAIN" in warning
    assert "make host-deps" in warning
