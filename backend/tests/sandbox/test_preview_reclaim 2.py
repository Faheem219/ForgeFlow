"""Port reclaim and pidfile ownership (phase-59).

The proxy routes to *fixed* container ports, so a preview restart has to guarantee "these ports are
mine" — not "I killed the PIDs I happened to remember". These tests pin the script the sandbox runs
and the decisions made from its output, without needing a Docker daemon.
"""

from __future__ import annotations

import shlex
import subprocess

from app.sandbox.ports import (
    BUSY_MARKER,
    PIDFILE_DIR,
    parse_busy_ports,
    parse_process_count,
    pidfile_path,
    reclaim_script,
    wrap_with_pidfile,
)


def test_the_reclaim_script_is_valid_shell() -> None:
    """It runs inside the sandbox as `sh -c`; a syntax error would be invisible until then."""
    script = reclaim_script([5173, 3001], kinds=["frontend", "backend"])

    result = subprocess.run(["sh", "-n"], input=script, text=True, capture_output=True)

    assert result.returncode == 0, result.stderr


def test_the_reclaim_script_targets_only_the_given_ports() -> None:
    script = reclaim_script([5173, 3001], kinds=["frontend"])

    assert "5173" in script and "3001" in script
    # /proc/net/tcp writes the local port in upper-case hex; 5173 == 0x1435, 3001 == 0x0BB9.
    assert "1435" in script and "0BB9" in script
    assert "8080" not in script  # nothing it was not asked for


def test_the_reclaim_script_kills_recorded_pidfiles_first() -> None:
    script = reclaim_script([5173], kinds=["frontend", "backend"])

    assert pidfile_path("frontend") in script
    assert pidfile_path("backend") in script
    assert "_kill_tree" in script


def test_an_empty_port_list_is_a_no_op() -> None:
    assert reclaim_script([]) == "exit 0"


def test_busy_ports_are_parsed_from_the_marker_lines() -> None:
    output = f"noise\n{BUSY_MARKER} 5173\nmore noise\n{BUSY_MARKER} 3001\n"

    assert parse_busy_ports(output) == [5173, 3001]


def test_a_clean_reclaim_reports_nothing_busy() -> None:
    assert parse_busy_ports("") == []
    assert parse_busy_ports("some unrelated output\n") == []


def test_a_malformed_marker_line_is_ignored() -> None:
    """Parsing must not turn sandbox noise into a phantom busy port."""
    assert parse_busy_ports(f"{BUSY_MARKER}\n{BUSY_MARKER} notaport\n") == []


# --------------------------------------------------------------------- pidfile wrapping


def test_the_wrapped_command_records_its_own_pid_then_execs() -> None:
    """`exec` is the point: the recorded PID must be the dev server, not a dead wrapper."""
    argv = wrap_with_pidfile("pnpm dev --port 5173", "frontend")

    assert argv[0] == "sh" and argv[1] == "-c"
    body = argv[2]
    assert "echo $$ >" in body
    assert body.rstrip().endswith("exec pnpm dev --port 5173")
    assert PIDFILE_DIR in body


def test_the_wrapped_command_is_valid_shell() -> None:
    argv = wrap_with_pidfile("pnpm dev --host 0.0.0.0 --port 5173 --strictPort", "frontend")

    result = subprocess.run(["sh", "-n"], input=argv[2], text=True, capture_output=True)

    assert result.returncode == 0, result.stderr


def test_pidfiles_live_under_the_forgeflow_dir() -> None:
    """Which is pruned from the workspace tree, so it never reaches the model or a deploy."""
    assert pidfile_path("frontend").startswith(".forgeflow/")
    assert shlex.quote(PIDFILE_DIR) == PIDFILE_DIR  # no quoting surprises in the script


# --------------------------------------------------------------------- process census


def test_the_process_count_is_read_from_the_last_line() -> None:
    assert parse_process_count("42\n") == 42
    assert parse_process_count("warning: something\n7\n") == 7


def test_an_unreadable_process_count_is_none_not_zero() -> None:
    """Zero would read as "no processes", which would silence the pressure warning."""
    assert parse_process_count("") is None
    assert parse_process_count("ls: cannot access\n") is None
