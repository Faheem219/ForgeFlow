"""In-memory Docker double for sandbox-manager tests.

Models only the surface the manager touches (containers get/run/list, volumes get/create/remove)
and tracks lifecycle transitions so tests can assert create → start/stop → remove behaviour
without a real daemon.
"""

from __future__ import annotations

import uuid
from typing import Any

from docker.errors import NotFound


class FakeContainer:
    def __init__(
        self,
        client: FakeDockerClient,
        name: str,
        image: str,
        labels: dict[str, str],
        status: str = "running",
        run_kwargs: dict[str, Any] | None = None,
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

    # test helper: pre-seed a container (as if a prior process created it)
    def seed(self, name: str, labels: dict[str, str], status: str = "running") -> FakeContainer:
        container = FakeContainer(
            self._client, name=name, image="img", labels=labels, status=status
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


class FakeNetworks:
    def __init__(self, client: FakeDockerClient) -> None:
        self._client = client
        self._store: dict[str, FakeNetwork] = {}

    def get(self, name: str) -> FakeNetwork:
        network = self._store.get(name)
        if network is None:
            raise NotFound(f"no such network: {name}")
        return network

    def create(self, **kwargs: Any) -> FakeNetwork:
        name = kwargs["name"]
        network = FakeNetwork(self._client, name, kwargs)
        self._store[name] = network
        self._client.network_create_calls.append(kwargs)
        return network


class FakeDockerClient:
    def __init__(self) -> None:
        self.containers = FakeContainers(self)
        self.volumes = FakeVolumes(self)
        self.networks = FakeNetworks(self)
        self.run_calls: list[dict[str, Any]] = []
        self.network_create_calls: list[dict[str, Any]] = []
