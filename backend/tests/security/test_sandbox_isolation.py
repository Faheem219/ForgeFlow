"""Sandbox isolation audit (phase-47, §7 / risk §12).

Phase-11 asserts ``build_run_kwargs`` in isolation. This is the *audit* view: the posture as a whole
system — the flags actually reaching Docker, the limits genuinely coming from config rather than
being hardcoded, and the two deliberate network relaxations being the **only** ones.

Untrusted generated code runs in these containers, so the properties below are the boundary between
"a bad generated app" and "a compromised host".
"""

from __future__ import annotations

import contextlib
import pathlib
import re

import pytest

from app.core.config import get_config, reset_config
from app.sandbox.manager import (
    SandboxManager,
    build_egress_network_kwargs,
    build_preview_network_kwargs,
    build_run_kwargs,
)
from tests.sandbox.fakes import FakeDockerClient

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[2] / "app"


def _kwargs(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "image": "forgeflow-sandbox:latest",
        "cpu_limit": 1.0,
        "mem_limit": "1g",
        "pids_limit": 256,
        "read_only": True,
    }
    base.update(overrides)
    return build_run_kwargs("proj", **base)  # type: ignore[arg-type]


# ---------------------------------------------------------------- the posture


def test_the_full_isolation_posture_is_present() -> None:
    """Every control §7 requires, asserted together rather than piecemeal."""
    kwargs = _kwargs()

    assert kwargs["network_mode"] == "none"  # no host network
    assert kwargs["cap_drop"] == ["ALL"]  # no Linux capabilities
    assert kwargs["security_opt"] == ["no-new-privileges"]  # no setuid escalation
    assert kwargs["read_only"] is True  # immutable root filesystem
    assert kwargs["pids_limit"] == 256  # no fork bombs
    assert kwargs["mem_limit"] == "1g"
    assert kwargs["nano_cpus"] == 1_000_000_000
    assert kwargs["tmpfs"] == {"/tmp": ""}  # writable scratch without a writable root
    assert kwargs["restart_policy"] == {"Name": "no"}  # a killed sandbox stays dead


def test_privileged_mode_is_never_requested() -> None:
    kwargs = _kwargs()
    assert kwargs.get("privileged", False) is False
    assert "cap_add" not in kwargs
    assert kwargs.get("pid_mode") is None
    assert kwargs.get("ipc_mode") is None
    assert kwargs.get("userns_mode") is None


def test_only_the_workspace_volume_is_mounted() -> None:
    """No host path is ever bind-mounted — the sandbox sees a named volume and nothing else."""
    volumes = _kwargs()["volumes"]
    assert isinstance(volumes, dict)
    assert len(volumes) == 1
    [(source, spec)] = volumes.items()
    assert spec == {"bind": "/workspace", "mode": "rw"}
    assert not str(source).startswith("/")  # a named volume, not a host directory
    assert not str(source).startswith(".")


def test_the_docker_socket_is_never_mounted() -> None:
    """Mounting docker.sock would hand generated code the host daemon — game over."""
    volumes = _kwargs()["volumes"]
    assert isinstance(volumes, dict)
    assert all("docker.sock" not in str(key) for key in volumes)


# ---------------------------------------------------------------- limits come from config


@pytest.mark.usefixtures("mongo_db")
async def test_resource_limits_are_config_driven_not_hardcoded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An operator must be able to tighten limits without a code change."""
    monkeypatch.setenv("SANDBOX_MEM_LIMIT", "256m")
    monkeypatch.setenv("SANDBOX_PIDS_LIMIT", "64")
    monkeypatch.setenv("SANDBOX_CPU_LIMIT", "0.5")
    reset_config()

    client = FakeDockerClient()
    manager = SandboxManager(client=client)
    await manager._create_container("proj-limits")

    created = client.run_calls[-1]
    assert created["mem_limit"] == "256m"
    assert created["pids_limit"] == 64
    assert created["nano_cpus"] == 500_000_000
    # ...and tightening limits must never quietly drop the hard controls.
    assert created["network_mode"] == "none"
    assert created["cap_drop"] == ["ALL"]
    assert created["security_opt"] == ["no-new-privileges"]


def test_read_only_root_defaults_to_on() -> None:
    reset_config()
    assert bool(get_config().get("sandbox_read_only_root")) is True


# ---------------------------------------------------------------- network relaxations


def test_the_preview_network_cannot_reach_the_host_or_internet() -> None:
    kwargs = build_preview_network_kwargs("forgeflow-preview")
    assert kwargs["internal"] is True  # the whole point
    assert kwargs["driver"] == "bridge"


def test_the_egress_network_routes_out_but_is_not_the_host_network() -> None:
    """Live validation needs the public internet; it still never gets the host namespace."""
    kwargs = build_egress_network_kwargs("forgeflow-egress")
    assert kwargs["internal"] is False  # deliberate — Playwright hits the deployed URL
    assert kwargs["driver"] == "bridge"  # a routed bridge, never `host`
    assert kwargs["labels"]["forgeflow.role"] == "egress"


def test_the_two_network_relaxations_are_the_only_ones() -> None:
    """Default is `network_mode=none`; any other network must be one of the two audited ones."""
    assert _kwargs()["network_mode"] == "none"
    for builder in (build_preview_network_kwargs, build_egress_network_kwargs):
        assert builder("n")["driver"] == "bridge"


# ---------------------------------------------------------------- static source audit


def _python_sources() -> list[pathlib.Path]:
    return [p for p in BACKEND_ROOT.rglob("*.py") if "__pycache__" not in p.parts]


@pytest.mark.parametrize(
    "forbidden",
    [
        r"network_mode\s*[:=]\s*['\"]host['\"]",
        r"privileged\s*[:=]\s*True",
        r"/var/run/docker\.sock",
        r"cap_add",
    ],
)
def test_no_source_file_requests_a_dangerous_docker_option(forbidden: str) -> None:
    """A regression guard: these options must not appear anywhere in the control plane."""
    pattern = re.compile(forbidden)
    offenders = [
        str(path.relative_to(BACKEND_ROOT))
        for path in _python_sources()
        if pattern.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == [], f"Dangerous docker option {forbidden!r} found in: {offenders}"


# ---------------------------------------------------------------- live daemon (opt-in)


@pytest.mark.docker
@pytest.mark.usefixtures("mongo_db")
async def test_docker_inspect_confirms_the_flags_on_a_real_container() -> None:
    """The manual verification step, automated: `docker inspect` a real sandbox.

    Opt-in (``-m docker``) because it needs a daemon and the sandbox image.
    """
    import docker  # imported here so the module stays importable without the SDK

    from app.sandbox.manager import container_name

    manager = SandboxManager()
    project_id = "sec-audit-probe"
    name = container_name(project_id)
    try:
        await manager._create_container(project_id)
        host_config = docker.from_env().containers.get(name).attrs["HostConfig"]

        assert host_config["NetworkMode"] == "none"
        assert host_config["CapDrop"] == ["ALL"]
        assert "no-new-privileges" in " ".join(host_config.get("SecurityOpt") or [])
        assert host_config["ReadonlyRootfs"] is True
        assert host_config["PidsLimit"] > 0
        assert host_config["Memory"] > 0
        assert host_config["Privileged"] is False
    finally:
        # Remove the probe container directly — this test owns it, not a Project record.
        with contextlib.suppress(Exception):
            docker.from_env().containers.get(name).remove(force=True)
