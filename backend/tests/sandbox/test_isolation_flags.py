"""The isolation posture lives in build_run_kwargs — assert it without a daemon (D5, §7)."""

from __future__ import annotations

from app.sandbox.manager import (
    SANDBOX_HOME,
    build_run_kwargs,
    build_sandbox_network_kwargs,
    container_name,
    home_volume_name,
    sandbox_network_name,
    volume_name,
)


def test_run_spec_enforces_isolation() -> None:
    kwargs = build_run_kwargs(
        "proj123",
        image="forgeflow-sandbox:latest",
        cpu_limit=1.5,
        mem_limit="1g",
        pids_limit=256,
        read_only=True,
    )

    # No host network (its own sealed per-project network), all caps dropped, no escalation.
    assert kwargs["network_mode"] == sandbox_network_name("proj123")
    assert build_sandbox_network_kwargs(sandbox_network_name("proj123"))["internal"] is True
    assert kwargs["cap_drop"] == ["ALL"]
    assert kwargs["security_opt"] == ["no-new-privileges"]

    # Resource caps applied.
    assert kwargs["read_only"] is True
    assert kwargs["pids_limit"] == 256
    assert kwargs["mem_limit"] == "1g"
    assert kwargs["nano_cpus"] == 1_500_000_000

    # Writable scratch + persistent per-project workspace volume.
    assert kwargs["tmpfs"] == {"/tmp": ""}
    assert kwargs["volumes"] == {
        volume_name("proj123"): {"bind": "/workspace", "mode": "rw"},
        home_volume_name("proj123"): {"bind": SANDBOX_HOME, "mode": "rw"},
    }

    # Deterministic naming + labels for reconciliation / reaping.
    assert kwargs["name"] == container_name("proj123")
    assert kwargs["labels"]["forgeflow.managed"] == "true"
    assert kwargs["labels"]["forgeflow.project"] == "proj123"
    assert kwargs["restart_policy"] == {"Name": "no"}


def test_read_only_root_is_configurable() -> None:
    kwargs = build_run_kwargs(
        "p",
        image="img",
        cpu_limit=1.0,
        mem_limit="512m",
        pids_limit=128,
        read_only=False,
    )
    assert kwargs["read_only"] is False
    assert kwargs["nano_cpus"] == 1_000_000_000


def test_the_home_volume_keeps_a_read_only_root_usable() -> None:
    """corepack/pnpm/npm write under $HOME; a read-only root without this mount kills every one.

    This is the regression guard for the `ENOENT … mkdir '/home/app/.cache/node/corepack/v1'`
    that made the sandbox unable to run any package-manager command at all.
    """
    kwargs = build_run_kwargs(
        "p", image="img", cpu_limit=1.0, mem_limit="1g", pids_limit=256, read_only=True
    )

    home = kwargs["volumes"][home_volume_name("p")]
    assert home == {"bind": SANDBOX_HOME, "mode": "rw"}
    # A named volume, not a tmpfs: the pnpm store must survive a restart and must not be charged
    # against the container's memory limit.
    assert SANDBOX_HOME not in kwargs["tmpfs"]
    assert kwargs["read_only"] is True  # …and the root stays read-only


def test_names_are_project_scoped() -> None:
    assert container_name("a") != container_name("b")
    assert sandbox_network_name("a") != sandbox_network_name("b")
    assert volume_name("a") != volume_name("b")
    assert home_volume_name("a") != home_volume_name("b")
    assert volume_name("a") != home_volume_name("a")
    assert container_name("a").endswith("a")
    assert volume_name("a").endswith("a")
