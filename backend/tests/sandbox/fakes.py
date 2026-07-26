"""In-memory Docker double for sandbox-manager tests.

Models only the surface the manager touches (containers get/run/list, volumes get/create/remove,
images get) and tracks lifecycle transitions so tests can assert create → start/stop → remove
behaviour without a real daemon. Containers also carry the ``attrs`` the manager inspects to decide
whether an existing sandbox predates the current image/mount spec.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from docker.errors import APIError, ImageNotFound, NotFound

from app.sandbox.manager import SANDBOX_HOME, WORKSPACE


def _mounts(destinations: Sequence[str]) -> list[dict[str, str]]:
    return [{"Destination": d} for d in destinations]


class FakeContainer:
    def __init__(
        self,
        client: FakeDockerClient,
        name: str,
        image: str,
        labels: dict[str, str],
        status: str = "running",
        run_kwargs: dict[str, Any] | None = None,
        mounts: Sequence[str] | None = None,
        image_id: str | None = None,
        network_mode: str = "",
    ) -> None:
        self._client = client
        self.name = name
        self.id = "cid-" + uuid.uuid4().hex[:12]
        self.image = image
        self.labels = labels
        self.status = status
        self.run_kwargs = run_kwargs or {}
        self.start_calls = 0
        self.stop_calls = 0
        self.remove_calls = 0
        binds: Sequence[str] = (
            mounts
            if mounts is not None
            else [spec["bind"] for spec in (self.run_kwargs.get("volumes") or {}).values()]
        )
        self.attrs: dict[str, Any] = {
            "Image": image_id or client.images.id_for(image),
            "Mounts": _mounts(binds),
            # "" models a container whose network mode we can't determine; the manager treats that
            # as "no signal" rather than as grounds for recreating it.
            "HostConfig": {
                "NetworkMode": network_mode or str(self.run_kwargs.get("network_mode", ""))
            },
        }

    def reload(self) -> None:  # docker refreshes .status; our state is already live
        pass

    def start(self) -> None:
        self.start_calls += 1
        self.status = "running"

    def stop(self, timeout: int = 10) -> None:
        self.stop_calls += 1
        self.status = "exited"

    def remove(self, force: bool = False) -> None:
        self.remove_calls += 1
        self._client.containers._store.pop(self.name, None)


class FakeContainers:
    def __init__(self, client: FakeDockerClient) -> None:
        self._client = client
        self._store: dict[str, FakeContainer] = {}

    def get(self, name: str) -> FakeContainer:
        container = self._store.get(name)
        if container is None:
            raise NotFound(f"no such container: {name}")
        return container

    def run(self, **kwargs: Any) -> FakeContainer:
        container = FakeContainer(
            self._client,
            name=kwargs["name"],
            image=kwargs["image"],
            labels=kwargs["labels"],
            status="running",
            run_kwargs=kwargs,
        )
        self._store[container.name] = container
        self._client.run_calls.append(kwargs)
        return container

    def list(self, all: bool = False, filters: dict[str, Any] | None = None) -> list[FakeContainer]:
        # `all`/`filters` mirror the docker SDK's containers.list() signature.
        return list(self._store.values())

    # test helper: pre-seed a container (as if a prior process created it). It gets the current
    # mount spec by default so `ensure` adopts it; pass `mounts=[…]` to model an outdated one.
    def seed(
        self,
        name: str,
        labels: dict[str, str],
        status: str = "running",
        image: str = "img",
        mounts: Sequence[str] | None = None,
        network_mode: str = "",
    ) -> FakeContainer:
        container = FakeContainer(
            self._client,
            name=name,
            image=image,
            labels=labels,
            status=status,
            mounts=mounts if mounts is not None else [WORKSPACE, SANDBOX_HOME],
            network_mode=network_mode,
        )
        self._store[name] = container
        return container


class FakeVolume:
    def __init__(self, client: FakeDockerClient, name: str, labels: dict[str, str]) -> None:
        self._client = client
        self.name = name
        self.labels = labels

    def remove(self, force: bool = False) -> None:
        self._client.volumes._store.pop(self.name, None)


class FakeVolumes:
    def __init__(self, client: FakeDockerClient) -> None:
        self._client = client
        self._store: dict[str, FakeVolume] = {}

    def get(self, name: str) -> FakeVolume:
        volume = self._store.get(name)
        if volume is None:
            raise NotFound(f"no such volume: {name}")
        return volume

    def create(self, name: str, labels: dict[str, str] | None = None) -> FakeVolume:
        volume = FakeVolume(self._client, name=name, labels=labels or {})
        self._store[name] = volume
        return volume


class FakeNetwork:
    def __init__(self, client: FakeDockerClient, name: str, kwargs: dict[str, Any]) -> None:
        self._client = client
        self.name = name
        self.kwargs = kwargs
        self.connected: list[FakeContainer] = []

    def _find(self, container_id: str) -> FakeContainer | None:
        for container in self._client.containers._store.values():
            if container.id == container_id:
                return container
        return None

    def connect(self, container_id: str) -> None:
        container = self._find(container_id)
        if container is not None and container not in self.connected:
            self.connected.append(container)

    def disconnect(self, container_id: str, force: bool = False) -> None:
        container = self._find(container_id)
        if container is not None and container in self.connected:
            self.connected.remove(container)

    def remove(self) -> None:
        # Docker refuses to remove a network that still has endpoints (even a stopped container's);
        # the orphan-prune relies on exactly that refusal.
        if self.connected:
            raise APIError(f"network {self.name} has active endpoints")
        self._client.networks._store.pop(self.name, None)
        self._client.networks.pruned += 1  # a freed subnet, in address-pool terms

    @property
    def labels(self) -> dict[str, str]:
        labels = self.kwargs.get("labels") or {}
        return dict(labels)


class FakeNetworks:
    def __init__(self, client: FakeDockerClient) -> None:
        self._client = client
        self._store: dict[str, FakeNetwork] = {}
        #: Models docker's finite address pools: refuse to create until an orphan is reclaimed.
        self.fail_create_until_pruned = False
        self.pruned = 0

    def get(self, name: str) -> FakeNetwork:
        network = self._store.get(name)
        if network is None:
            raise NotFound(f"no such network: {name}")
        return network

    def create(self, **kwargs: Any) -> FakeNetwork:
        if self.fail_create_until_pruned and self.pruned == 0:
            raise APIError("all predefined address pools have been fully subnetted")
        name = kwargs["name"]
        network = FakeNetwork(self._client, name, kwargs)
        self._store[name] = network
        self._client.network_create_calls.append(kwargs)
        return network

    def list(self, filters: dict[str, Any] | None = None) -> list[FakeNetwork]:
        """`filters={"label": "k=v"}`, mirroring the docker SDK's networks.list()."""
        label = (filters or {}).get("label")
        networks = list(self._store.values())
        if not label:
            return networks
        key, _, value = str(label).partition("=")
        return [n for n in networks if n.labels.get(key) == value]


class FakeImage:
    def __init__(self, image_id: str) -> None:
        self.id = image_id


class FakeImages:
    """Tag → image-id, so a *rebuild* (same tag, new id) is expressible."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    def id_for(self, tag: str) -> str:
        return self._store.get(tag, f"sha256:{tag}")

    def get(self, tag: str) -> FakeImage:
        image_id = self._store.get(tag)
        if image_id is None:
            raise ImageNotFound(f"no such image: {tag}")
        return FakeImage(image_id)

    # test helper: (re)build a tag — a fresh id, as `docker build` would produce
    def register(self, tag: str) -> FakeImage:
        self._store[tag] = "sha256:" + uuid.uuid4().hex[:12]
        return FakeImage(self._store[tag])


class FakeDockerClient:
    def __init__(self) -> None:
        self.images = FakeImages()
        self.containers = FakeContainers(self)
        self.volumes = FakeVolumes(self)
        self.networks = FakeNetworks(self)
        self.run_calls: list[dict[str, Any]] = []
        self.network_create_calls: list[dict[str, Any]] = []
