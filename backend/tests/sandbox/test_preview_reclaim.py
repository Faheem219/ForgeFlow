"""Port reclaim and pidfile ownership (phase-59).

The proxy routes to *fixed* container ports, so a preview restart has to guarantee "these ports are
mine" — not "I killed the PIDs I happened to remember". These tests pin the script the sandbox runs
and the decisions made from its output, without needing a Docker daemon.
"""

from __future__ import annotations

import contextlib
import os
import shlex
import shutil
import subprocess
import sys
import time

import pytest

from app.sandbox.ports import (
    BUSY_MARKER,
    KILL_TREE_FUNCTIONS,
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


# --------------------------------------------------------------------- the process-tree walk
#
# These execute the real shell against real processes. A container run caught what string matching
# could not: the first _kill_tree was recursive, and POSIX sh has no lexical scope, so recursing
# clobbered the caller's `_p` and the OUTERMOST process survived. The port still freed (its child
# died), so every port-level check passed while orphans accumulated toward the PID ceiling — which
# is the failure this module exists to prevent.

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("pgrep") is None,
    reason="needs a POSIX shell with pgrep",
)


def _spawn_parent_with_child() -> subprocess.Popen[bytes]:
    """A parent that keeps running after its child dies — the pnpm→vite shape.

    The `exec` and the foreground `sleep` are both load-bearing. An earlier fixture used
    ``sleep 30 & wait``, where killing the child makes ``wait`` return and the parent exits *by
    itself* — so the test passed against the recursive implementation it was written to catch.
    Here the parent's own lifetime is independent of the child's, so only an actual kill ends it.
    """
    return subprocess.Popen(  # noqa: S602 - a fixed, literal command
        ["sh", "-c", "sleep 30 & exec sleep 30"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _alive(pid: int) -> bool:
    """Running — not gone, and not a zombie.

    ``kill -0`` alone says "alive" for a zombie: a process already SIGKILLed but not yet reaped.
    Once the parent dies its killed child is re-parented to whichever process adopts orphans, and
    on a CI runner that adopter reaps lazily — so the child lingers as a zombie and a plain
    ``kill -0`` reported a correctly killed process as a survivor.
    """
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as handle:
            # "pid (comm) state ..." — comm may contain spaces/parens, so split on the last ") ".
            state = handle.read().rsplit(") ", 1)[1].split()[0]
    except (OSError, IndexError):
        return True  # no /proc to consult: trust kill -0
    return state != "Z"


def _gone(pid: int, timeout: float = 5.0) -> bool:
    """Wait for ``pid`` to stop running: SIGKILL is delivered asynchronously."""
    deadline = time.monotonic() + timeout
    while _alive(pid):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)
    return True


def _child_pids(pid: int) -> list[int]:
    out = subprocess.run(
        ["pgrep", "-P", str(pid)], capture_output=True, text=True, check=False
    ).stdout
    return [int(line) for line in out.split() if line.isdigit()]


def test_the_kill_walk_removes_the_parent_not_only_its_child() -> None:
    parent = _spawn_parent_with_child()
    try:
        deadline = time.monotonic() + 5
        children: list[int] = []
        while time.monotonic() < deadline and not children:
            children = _child_pids(parent.pid)
            if not children:
                time.sleep(0.05)
        assert children, "the test fixture never spawned a child"

        subprocess.run(
            ["sh", "-c", f"{KILL_TREE_FUNCTIONS}\n_kill_tree {parent.pid}"],
            check=True,
            capture_output=True,
        )

        parent.wait(timeout=5)
        assert not _alive(parent.pid), "the parent survived — the recursion bug is back"
        for child in children:
            assert _gone(child), f"child {child} survived"
    finally:
        with contextlib.suppress(Exception):
            parent.kill()
            parent.wait(timeout=5)


def test_the_kill_walk_tolerates_an_empty_pid() -> None:
    """A missing or unreadable pidfile must be a no-op, never an error that aborts the reclaim."""
    result = subprocess.run(
        ["sh", "-c", f'{KILL_TREE_FUNCTIONS}\n_kill_tree ""'],
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0


def test_the_kill_walk_tolerates_a_pid_that_is_already_gone() -> None:
    parent = _spawn_parent_with_child()
    parent.kill()
    parent.wait(timeout=5)

    result = subprocess.run(
        ["sh", "-c", f"{KILL_TREE_FUNCTIONS}\n_kill_tree {parent.pid}"],
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
