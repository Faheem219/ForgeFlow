"""The isolation posture lives in build_run_kwargs — assert it without a daemon (D5, §7)."""

from __future__ import annotations

from app.sandbox.manager import build_run_kwargs, container_name, volume_name


def test_run_spec_enforces_isolation() -> None:
    kwargs = build_run_kwargs(
        "proj123",
        image="forgeflow-sandbox:latest",
        cpu_limit=1.5,
        mem_limit="1g",
        pids_limit=256,
        read_only=True,
    )

    # No host network, all caps dropped, no privilege escalation.
    assert kwargs["network_mode"] == "none"
    assert kwargs["cap_drop"] == ["ALL"]
    assert kwargs["security_opt"] == ["no-new-privileges"]

    # Resource caps applied.
    assert kwargs["read_only"] is True
    assert kwargs["pids_limit"] == 256
    assert kwargs["mem_limit"] == "1g"
    assert kwargs["nano_cpus"] == 1_500_000_000

    # Writable scratch + persistent per-project workspace volume.
    assert kwargs["tmpfs"] == {"/tmp": ""}
    assert kwargs["volumes"] == {volume_name("proj123"): {"bind": "/workspace", "mode": "rw"}}

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


def test_names_are_project_scoped() -> None:
    assert container_name("a") != container_name("b")
    assert volume_name("a") != volume_name("b")
    assert container_name("a").endswith("a")
    assert volume_name("a").endswith("a")
