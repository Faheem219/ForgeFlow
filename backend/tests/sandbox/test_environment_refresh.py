"""A sandbox that predates the current image/mount spec must be rebuilt, not adopted.

Sandboxes are long-lived: the container is created once and reused for the life of the project. That
made an *image* fix unreachable — the sandbox that could not run `pnpm` (no writable $HOME, no baked
corepack) would keep failing forever, because `ensure` only ever started what was already there. So
`ensure` now replaces a container whose mounts or image no longer match, and the code survives
because it lives on the named volumes rather than in the container.
"""

from __future__ import annotations

import pytest
from beanie import PydanticObjectId

from app.core.config import get_config
from app.db.models import Project
from app.sandbox.manager import (
    SANDBOX_HOME,
    WORKSPACE,
    SandboxManager,
    container_name,
    home_volume_name,
    sandbox_network_name,
    volume_name,
)
from tests.sandbox.fakes import FakeDockerClient

pytestmark = pytest.mark.usefixtures("mongo_db")

LABELS_KEY = "forgeflow.project"


async def _project() -> Project:
    return await Project(user_id=PydanticObjectId(), name="app", app_db_name="db").insert()


def _labels(pid: str) -> dict[str, str]:
    return {"forgeflow.managed": "true", LABELS_KEY: pid}


async def test_a_container_without_the_writable_home_is_recreated() -> None:
    client = FakeDockerClient()
    manager = SandboxManager(client=client)
    project = await _project()
    pid = str(project.id)

    # A sandbox from before the fix: workspace mounted, no $HOME volume.
    stale = client.containers.seed(container_name(pid), _labels(pid), mounts=[WORKSPACE])

    info = await manager.ensure(project)

    assert info.container_id != stale.id, "the outdated container should have been replaced"
    assert stale.remove_calls == 1
    # …and the replacement has both mounts.
    binds = {spec["bind"] for spec in client.run_calls[0]["volumes"].values()}
    assert binds == {WORKSPACE, SANDBOX_HOME}


async def test_recreating_preserves_the_workspace_volume() -> None:
    """The whole point: fixing the environment must not delete the user's generated code."""
    client = FakeDockerClient()
    manager = SandboxManager(client=client)
    project = await _project()
    pid = str(project.id)

    await manager._ensure_volumes(pid)
    workspace_volume = client.volumes.get(volume_name(pid))
    client.containers.seed(container_name(pid), _labels(pid), mounts=[WORKSPACE])

    await manager.ensure(project)

    assert client.volumes.get(volume_name(pid)) is workspace_volume
    assert home_volume_name(pid) in client.volumes._store


async def test_a_container_still_on_the_old_none_network_is_recreated() -> None:
    """`network_mode=none` cannot be attached to ANY network, so such a sandbox is unusable.

    Docker refuses: "container cannot be connected to multiple networks with one of the networks in
    private (none) mode" — which means no preview proxy and no dependency installs. The replacement
    is created on the project's own internal network, which *can* take preview/egress on top.
    """
    client = FakeDockerClient()
    manager = SandboxManager(client=client)
    project = await _project()
    pid = str(project.id)

    stale = client.containers.seed(container_name(pid), _labels(pid), network_mode="none")

    info = await manager.ensure(project)

    assert info.container_id != stale.id
    assert client.run_calls[0]["network_mode"] == sandbox_network_name(pid)
    # …and that network is sealed: internal, so no route to the host or the internet.
    assert client.networks._store[sandbox_network_name(pid)].kwargs["internal"] is True


async def test_the_sandbox_network_is_per_project_and_dropped_with_the_container() -> None:
    client = FakeDockerClient()
    manager = SandboxManager(client=client)
    project = await _project()
    pid = str(project.id)

    await manager.ensure(project)
    assert sandbox_network_name(pid) in client.networks._store

    await manager.destroy(project)

    # Nothing else is ever on it, and docker's default address pools are finite.
    assert sandbox_network_name(pid) not in client.networks._store


async def test_orphaned_sandbox_networks_are_pruned_but_live_ones_are_kept() -> None:
    """Docker's address pools are finite — networks left by dead containers must not pile up."""
    client = FakeDockerClient()
    manager = SandboxManager(client=client)
    live = await _project()
    dead = await _project()

    await manager.ensure(live)
    await manager.ensure(dead)
    # Model a crash: the container is gone, its network is not.
    client.containers.get(container_name(str(dead.id))).remove()
    # The live one still holds an endpoint, which is how docker knows to refuse.
    client.networks.get(sandbox_network_name(str(live.id))).connect(
        client.containers.get(container_name(str(live.id))).id
    )

    pruned = await manager.prune_orphan_networks()

    assert pruned == 1
    assert sandbox_network_name(str(dead.id)) not in client.networks._store
    assert sandbox_network_name(str(live.id)) in client.networks._store
    # The shared preview/egress networks are never candidates (different role label).
    assert await manager.ensure(dead)  # and the pruned project just gets a fresh one


async def test_reconcile_reaps_sandboxes_whose_project_is_gone() -> None:
    """An unreachable container still holds a subnet — and docker's pools run out at ~30."""
    client = FakeDockerClient()
    manager = SandboxManager(client=client)
    live = await _project()
    ghost_id = str(PydanticObjectId())  # a project that no longer exists

    await manager.ensure(live)
    client.containers.seed(container_name(ghost_id), _labels(ghost_id))
    client.networks.create(**{"name": sandbox_network_name(ghost_id), "labels": {}})

    await manager.reconcile()

    assert container_name(ghost_id) not in client.containers._store
    assert container_name(str(live.id)) in client.containers._store  # the live one is untouched
    # Volumes are never removed as a side effect of startup — losing code must be deliberate.
    assert volume_name(ghost_id) not in client.volumes._store


async def test_reconcile_reaps_nothing_when_the_project_list_is_empty() -> None:
    """ "No projects" is indistinguishable from "wrong meta DB" — so it must never wipe the host."""
    client = FakeDockerClient()
    manager = SandboxManager(client=client)
    unknown = str(PydanticObjectId())
    client.containers.seed(container_name(unknown), _labels(unknown))

    await manager.reconcile()  # the DB has no projects at all

    assert container_name(unknown) in client.containers._store


async def test_a_full_address_pool_prunes_and_retries_instead_of_failing() -> None:
    """Pool exhaustion is a *when* on a busy host, so it must self-heal, not dead-end."""
    client = FakeDockerClient()
    manager = SandboxManager(client=client)
    project = await _project()

    # An orphaned network from a previous sandbox, and a daemon that refuses until it is gone.
    client.networks.create(
        **{"name": "forgeflow-sbnet-old", "labels": {"forgeflow.role": "sandbox"}}
    )
    client.networks.fail_create_until_pruned = True

    info = await manager.ensure(project)

    assert info.container_id is not None  # the sandbox came up anyway
    assert "forgeflow-sbnet-old" not in client.networks._store  # …by reclaiming the orphan
    assert sandbox_network_name(str(project.id)) in client.networks._store


async def test_a_rebuilt_image_replaces_the_running_container() -> None:
    client = FakeDockerClient()
    manager = SandboxManager(client=client)
    project = await _project()
    image = str(get_config().get("sandbox_image"))

    client.images.register(image)  # the image the sandbox was created from
    first = await manager.ensure(project)
    client.images.register(image)  # `make sandbox-build` → same tag, new id

    second = await manager.ensure(project)

    assert second.container_id != first.container_id
    assert len(client.run_calls) == 2


async def test_a_current_container_is_adopted_not_churned() -> None:
    client = FakeDockerClient()
    manager = SandboxManager(client=client)
    project = await _project()
    image = str(get_config().get("sandbox_image"))
    client.images.register(image)

    first = await manager.ensure(project)
    second = await manager.ensure(project)

    assert second.container_id == first.container_id
    assert len(client.run_calls) == 1


async def test_an_unknown_image_is_never_grounds_for_recreation() -> None:
    """If the daemon can't resolve the configured image, leave the sandbox alone."""
    client = FakeDockerClient()  # no images registered → images.get raises
    manager = SandboxManager(client=client)
    project = await _project()

    first = await manager.ensure(project)
    second = await manager.ensure(project)

    assert second.container_id == first.container_id
    assert len(client.run_calls) == 1


async def test_destroy_with_volumes_removes_the_home_cache_too() -> None:
    client = FakeDockerClient()
    manager = SandboxManager(client=client)
    project = await _project()
    pid = str(project.id)

    await manager.ensure(project)
    assert home_volume_name(pid) in client.volumes._store

    await manager.destroy(project, remove_volume=True)

    assert volume_name(pid) not in client.volumes._store
    assert home_volume_name(pid) not in client.volumes._store
