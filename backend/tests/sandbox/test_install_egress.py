"""Registry egress for dependency installs: narrow in scope, narrow in time.

A sandbox has no network, but the generated app's dependencies are not baked into the image, so an
install has to reach the npm registry. The window that grants it must open for install commands
*only*, must always close, and must survive overlapping installs.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.sandbox import network
from app.sandbox.exec import wants_registry
from app.sandbox.network import egress_window, needs_registry, open_windows
from app.sandbox.runtime import DockerRuntime, LocalRuntime


class FakeSandbox:
    def __init__(self, fail_attach: bool = False) -> None:
        self.events: list[str] = []
        self.attached = False
        self._fail_attach = fail_attach

    async def attach_egress_network(self, project_id: str) -> bool:
        if self._fail_attach:
            raise RuntimeError("daemon says no")
        self.events.append("attach")
        self.attached = True
        return True

    async def detach_egress_network(self, project_id: str) -> None:
        self.events.append("detach")
        self.attached = False


@pytest.fixture(autouse=True)
def _clean_windows() -> None:
    network.reset_windows()


@pytest.mark.parametrize(
    "argv",
    [
        ["pnpm", "install"],
        ["pnpm", "install", "--prefer-offline"],
        ["pnpm", "add", "zod"],
        ["pnpm", "-r", "install"],
        ["pnpm", "--filter", "frontend", "add", "clsx"],
        ["npm", "ci"],
        ["npm", "install", "--save-dev", "vitest"],
        ["yarn"],
        ["npx", "shadcn", "init"],
        ["corepack", "prepare", "pnpm@9.12.0"],
        ["/usr/local/bin/pnpm", "install"],
    ],
)
def test_fetching_commands_get_the_registry(argv: list[str]) -> None:
    assert needs_registry(argv) is True


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["pnpm", "dev"],
        ["pnpm", "build"],
        ["pnpm", "test"],
        ["pnpm", "exec", "vitest", "run"],
        ["pnpm", "exec", "playwright", "test"],
        ["pnpm", "--version"],
        ["node", "dist/index.js"],
        ["git", "status"],
        ["curl", "https://example.com"],  # generated code must not get a network this way
    ],
)
def test_everything_else_keeps_the_no_network_default(argv: list[str]) -> None:
    assert needs_registry(argv) is False


async def test_the_window_opens_and_always_closes() -> None:
    sandbox = FakeSandbox()

    async with egress_window("p1", manager=sandbox) as attached:
        assert attached is True
        assert sandbox.attached is True

    assert sandbox.events == ["attach", "detach"]
    assert sandbox.attached is False
    assert open_windows("p1") == 0


async def test_the_window_closes_when_the_command_explodes() -> None:
    sandbox = FakeSandbox()

    with pytest.raises(RuntimeError):
        async with egress_window("p1", manager=sandbox):
            raise RuntimeError("install blew up")

    assert sandbox.events == ["attach", "detach"]
    assert open_windows("p1") == 0


async def test_the_window_closes_on_cancellation() -> None:
    sandbox = FakeSandbox()

    async def held() -> None:
        async with egress_window("p1", manager=sandbox):
            await asyncio.sleep(10)

    task = asyncio.create_task(held())
    await asyncio.sleep(0)  # let it open the window
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert sandbox.events == ["attach", "detach"]
    assert sandbox.attached is False


async def test_overlapping_installs_share_one_route() -> None:
    """A finishing install must not pull the network out from under one still running."""
    sandbox = FakeSandbox()
    inner_done = asyncio.Event()

    async def outer() -> None:
        async with egress_window("p1", manager=sandbox):
            await inner_done.wait()
            assert sandbox.attached is True, "the route was revoked mid-install"

    async def inner() -> None:
        async with egress_window("p1", manager=sandbox):
            pass
        inner_done.set()

    await asyncio.gather(outer(), inner())

    assert sandbox.events == ["attach", "detach"]  # attached once, detached once
    assert open_windows("p1") == 0


async def test_windows_are_per_project() -> None:
    sandbox = FakeSandbox()

    async with egress_window("p1", manager=sandbox):
        assert open_windows("p1") == 1
        assert open_windows("p2") == 0
        async with egress_window("p2", manager=sandbox):
            assert open_windows("p2") == 1

    assert sandbox.events == ["attach", "attach", "detach", "detach"]


async def test_an_attach_failure_still_runs_the_command() -> None:
    """Better a readable registry error in the terminal than a docker traceback."""
    sandbox = FakeSandbox(fail_attach=True)

    async with egress_window("p1", manager=sandbox) as attached:
        assert attached is False

    assert sandbox.events == ["detach"]
    assert open_windows("p1") == 0


# --------------------------------------------------------------- the exec-service gate


def test_exec_opens_a_window_only_for_a_containerised_install(tmp_path: Path) -> None:
    container_backed = DockerRuntime(object())
    local = LocalRuntime(str(tmp_path))

    assert wants_registry(container_backed, ["pnpm", "install"]) is True
    assert wants_registry(container_backed, ["pnpm", "dev"]) is False
    # The local test backend has no docker networking to borrow.
    assert wants_registry(local, ["pnpm", "install"]) is False


def test_the_relaxation_can_be_switched_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SANDBOX_INSTALL_NETWORK", "false")
    from app.core.config import reset_config

    reset_config()
    assert wants_registry(DockerRuntime(object()), ["pnpm", "install"]) is False
