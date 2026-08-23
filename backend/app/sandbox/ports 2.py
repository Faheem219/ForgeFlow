"""Deterministic port reclaim inside the sandbox (phase-59).

The reverse proxy routes ``*.preview.localhost`` to fixed container ports, so *"these two ports are
mine"* is the property a preview restart must guarantee. The previous implementation guaranteed
something weaker and untrue: it killed the PIDs it happened to remember in process memory
(``PreviewService._state``). In dev the control plane runs ``uvicorn --reload``, so **every backend
edit wiped that dict** and orphaned whatever was running inside the sandbox — after which Vite finds
5173 taken, slides to 5175, reports itself healthy, and the proxy serves nothing.

Reclaiming *by port* holds regardless of who started the process or which control-plane process is
alive now. Two mechanisms, in order:

1. ``fuser -k -n tcp <port>`` — precise, needs ``psmisc`` (added to the image in phase-59).
2. A pure ``/proc`` scan — no packages at all, so a container still running the *old* image (they
   outlive the control plane) degrades instead of failing.

Both are followed by a re-scan, because a reclaim that silently did nothing is exactly the failure
this module exists to prevent: the caller needs to know the port is genuinely free before it starts
a dev server that would otherwise slide to another one.

The scripts are built as pure strings so they are unit-testable without a Docker daemon, and they
only ever touch the ports handed to them.
"""

from __future__ import annotations

import shlex

#: Where each preview process records its own PID, workspace-relative. Written by the process
#: itself (``echo $$`` before ``exec``), so it survives a control-plane restart and needs no
#: docker-specific PID lookup.
PIDFILE_DIR = ".forgeflow/preview"

#: Marker the reclaim script prints for a port that is still listening after every attempt.
BUSY_MARKER = "FORGEFLOW_PORT_BUSY"


def pidfile_path(kind: str) -> str:
    """Workspace-relative pidfile for a preview process kind (``frontend``/``backend``)."""
    return f"{PIDFILE_DIR}/{kind}.pid"


def wrap_with_pidfile(command: str, kind: str) -> list[str]:
    """Wrap a dev-server command so it records its own PID, then *becomes* the server.

    ``exec`` matters: the shell is replaced, so the recorded ``$$`` is the dev server's own PID and
    not a wrapper that dies leaving an unkillable orphan.
    """
    pidfile = shlex.quote(pidfile_path(kind))
    directory = shlex.quote(PIDFILE_DIR)
    return [
        "sh",
        "-c",
        f"mkdir -p {directory} && echo $$ > {pidfile} && exec {command}",
    ]


def _port_hex(port: int) -> str:
    """``/proc/net/tcp`` writes the local port as upper-case hex."""
    return f"{port:04X}"


def _kill_listeners_snippet(port: int) -> str:
    """Kill whatever holds a LISTEN socket on ``port`` — ``fuser`` first, else a /proc scan.

    In ``/proc/net/tcp`` the columns are ``sl local_address rem_address st ... inode``: state
    ``0A`` is LISTEN and field 10 is the socket inode, which appears in the owning process's fd
    table as ``socket:[<inode>]``. That mapping is the whole fallback.
    """
    hexport = _port_hex(port)
    return f"""
  if command -v fuser >/dev/null 2>&1; then
    fuser -k -n tcp {port} >/dev/null 2>&1 || true
  fi
  if _busy {port}; then
    for ino in $(awk '$4=="0A" && $2 ~ /:{hexport}$/ {{print $10}}' \\
        /proc/net/tcp /proc/net/tcp6 2>/dev/null); do
      for fd in /proc/[0-9]*/fd/*; do
        link=$(readlink "$fd" 2>/dev/null) || continue
        if [ "$link" = "socket:[$ino]" ]; then
          pid=${{fd#/proc/}}; pid=${{pid%%/*}}
          kill -9 "$pid" 2>/dev/null || true
        fi
      done
    done
  fi
"""


def reclaim_script(ports: list[int], *, kinds: list[str] | None = None) -> str:
    """A ``sh`` script that frees ``ports`` and reports any it could not.

    Recorded pidfiles are killed first (tree-wise, so a ``pnpm`` parent cannot leave its ``vite``
    child holding the socket), then the ports themselves. Anything still listening at the end is
    printed as ``FORGEFLOW_PORT_BUSY <port>`` — the caller turns that into a real error rather than
    letting a dev server quietly relocate.
    """
    if not ports:
        return "exit 0"

    pidfiles = " ".join(shlex.quote(pidfile_path(k)) for k in (kinds or []))
    busy_checks = "".join(f"""
if _busy {port}; then echo "{BUSY_MARKER} {port}"; fi""" for port in ports)
    return f"""set -u

# LISTEN (state 0A) on this port, by hex suffix in /proc/net/tcp{{,6}}.
_busy() {{
  awk -v pat=":$(printf '%04X' "$1")\\$" '$4=="0A" && $2 ~ pat {{f=1}} END{{exit f?0:1}}' \\
    /proc/net/tcp /proc/net/tcp6 2>/dev/null
}}

# Kill a PID and everything under it: pgrep -P walks one level, so recurse to reach
# pnpm -> vite -> esbuild. Killing only the parent is what leaves the socket held.
_kill_tree() {{
  _p=$1
  [ -n "$_p" ] || return 0
  if command -v pkill >/dev/null 2>&1; then
    for _c in $(pgrep -P "$_p" 2>/dev/null); do _kill_tree "$_c"; done
  else
    for _s in /proc/[0-9]*/stat; do
      _c=$(cut -d' ' -f1 "$_s" 2>/dev/null) || continue
      _pp=$(sed -e 's/.*) . //' "$_s" 2>/dev/null | cut -d' ' -f1) || continue
      [ "$_pp" = "$_p" ] && _kill_tree "$_c"
    done
  fi
  kill -9 "$_p" 2>/dev/null || true
}}

for _f in {pidfiles or '""'}; do
  [ -n "$_f" ] && [ -f "$_f" ] || continue
  _kill_tree "$(cat "$_f" 2>/dev/null)"
  rm -f "$_f" 2>/dev/null || true
done
{"".join(_kill_listeners_snippet(p) for p in ports)}
{busy_checks}
exit 0
"""


def parse_busy_ports(output: str) -> list[int]:
    """Ports the reclaim script reported as still listening."""
    busy: list[int] = []
    for line in output.splitlines():
        parts = line.strip().split()
        if len(parts) == 2 and parts[0] == BUSY_MARKER and parts[1].isdigit():
            busy.append(int(parts[1]))
    return busy


#: Counts processes in the sandbox — a runaway count is what surfaces later as node's inscrutable
#: ``uv_thread_create`` assertion, so it is worth seeing directly (phase-59).
PROCESS_COUNT_SCRIPT = "ls -d /proc/[0-9]* 2>/dev/null | wc -l"


def parse_process_count(output: str) -> int | None:
    text = output.strip().splitlines()
    if not text or not text[-1].strip().isdigit():
        return None
    return int(text[-1].strip())


__all__ = [
    "BUSY_MARKER",
    "PIDFILE_DIR",
    "PROCESS_COUNT_SCRIPT",
    "parse_busy_ports",
    "parse_process_count",
    "pidfile_path",
    "reclaim_script",
    "wrap_with_pidfile",
]
