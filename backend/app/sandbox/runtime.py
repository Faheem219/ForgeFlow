"""Workspace runtime backends (phase-12).

A ``WorkspaceRuntime`` is a thin, backend-native filesystem+git surface rooted at the
project's ``/workspace``. All paths are workspace-relative (``""`` == root); each backend maps
them into its own namespace and confines operations to the workspace.

Two backends:

- :class:`DockerRuntime` — **production**. Every operation goes through the container via the
  Docker exec/archive APIs; untrusted files never touch the control-plane host (§7).
- :class:`LocalRuntime` — **tests only**. Real Python filesystem + real ``git`` inside an
  isolated temp directory, so FS round-trip and git-diff correctness are covered without a
  Docker daemon. Never used by the control plane at runtime.

Runtime methods are synchronous (the Docker SDK is sync; local IO is sync); the async service
layer off-loads them via :func:`asyncio.to_thread`.
"""

from __future__ import annotations

import io
import os
import posixpath
import queue
import shutil
import signal
import subprocess
import sys
import tarfile
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from docker.errors import NotFound

# SystemError shadows the builtin (taxonomy name fixed by the plan); A004 suppressed on-line.
from app.core.config import subprocess_env
from app.core.errors import SystemError, UserError  # noqa: A004

DOCKER_WORKSPACE = "/workspace"
#: Never surfaced in the workspace tree. ``.forgeflow`` is ForgeFlow's own in-workspace bookkeeping
#: (preview pidfiles, phase-59) — it is not the user's code, must not reach the model's context, and
#: must not be uploaded with a deploy.
_PRUNE_DIRS = {"node_modules", ".git", ".forgeflow"}

#: Returned by :meth:`ExecHandle.wait` when the exit code cannot be read — a container that died
#: mid-exec reports ``ExitCode: null``, which must never be coerced to 0 (= success). Distinct and
#: negative so consumers that check ``exit_code == 0`` correctly see a failure (phase-55 task 4).
EXIT_UNKNOWN = -1

#: The sandbox's non-root user (``sandbox/Dockerfile`` pins it to uid/gid 1000). The archive API
#: extracts as root and honours the tar's ownership, so a tar built with the default uid 0 lands
#: root-owned inside the container — read-only to everything the generated app actually runs as.
SANDBOX_UID = 1000
SANDBOX_GID = 1000
SANDBOX_USER = "app"

# POSIX-only, and this module's production backend (:class:`DockerRuntime`) only ever runs on
# Linux. Spelled as ``sys.platform`` rather than ``os.name`` because the type checker narrows on
# it, so the else-branch is analysed against the platform the code actually ships to.
if sys.platform == "win32":  # pragma: no cover - the runtime target is Linux
    fcntl = None
    pty = None
    struct = None
    termios = None
else:
    import fcntl
    import pty
    import struct
    import termios


@dataclass(frozen=True)
class ExecResult:
    exit_code: int
    stdout: bytes
    stderr: bytes

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    def text(self) -> str:
        return self.stdout.decode("utf-8", errors="replace")


@dataclass(frozen=True)
class NodeInfo:
    """A single workspace tree entry (path is workspace-root-relative, POSIX-separated)."""

    path: str
    is_dir: bool
    size: int


StreamName = Literal["stdout", "stderr"]


@dataclass(frozen=True)
class ExecChunk:
    stream: StreamName
    data: bytes


class ExecHandle(Protocol):
    """A running non-interactive command."""

    def stream(self) -> Iterator[ExecChunk]:
        """Blocking generator of output chunks, ending when the process closes its pipes."""
        ...

    def wait(self) -> int:
        """The exit code (call after :meth:`stream` is exhausted)."""
        ...

    def kill(self) -> None:
        """Terminate the process (and its children) — used for timeout + cancellation."""
        ...


class TtyHandle(Protocol):
    """An interactive PTY-backed shell."""

    def read(self, size: int = 4096) -> bytes:
        """Blocking read of TTY output; returns ``b""`` at EOF."""
        ...

    def write(self, data: bytes) -> None: ...
    def resize(self, cols: int, rows: int) -> None: ...
    def close(self) -> None:
        """Kill the shell and release the PTY (must reap the process)."""
        ...


class WorkspaceRuntime(Protocol):
    @property
    def workspace_root(self) -> str:
        """The resolved absolute root, in this backend's namespace (for the escape check)."""
        ...

    def read_bytes(self, rel: str) -> bytes: ...
    def write_bytes(self, rel: str, data: bytes) -> None: ...
    def delete(self, rel: str) -> None: ...
    def mkdir(self, rel: str) -> None: ...
    def move(self, src: str, dst: str) -> None: ...
    def size(self, rel: str) -> int: ...
    def exists(self, rel: str) -> bool: ...
    def real_path(self, rel: str) -> str: ...
    def list_tree(self, start: str, depth: int) -> list[NodeInfo]: ...
    def run_git(self, args: list[str], stdin: bytes | None = None) -> ExecResult: ...
    def start_exec(
        self, argv: list[str], cwd: str = "", env: dict[str, str] | None = None
    ) -> ExecHandle: ...
    def start_tty(
        self, argv: list[str], cwd: str = "", cols: int = 80, rows: int = 24
    ) -> TtyHandle: ...


def _pump_pipe(fd: int, name: StreamName, sink: queue.Queue[ExecChunk | None]) -> None:
    """Forward a pipe to the queue using unbuffered reads (so output streams live)."""
    try:
        while True:
            chunk = os.read(fd, 4096)
            if not chunk:
                break
            sink.put(ExecChunk(name, chunk))
    except OSError:
        pass
    finally:
        sink.put(None)


def _set_winsize(fd: int, cols: int, rows: int) -> None:
    if fcntl is None or struct is None or termios is None:
        raise SystemError("PTY resize is not supported on Windows")
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


# --------------------------------------------------------------------------- tar helpers (pure)


def make_tar(name: str, data: bytes) -> bytes:
    """Build an in-memory tar carrying a single file member ``name`` → ``data``.

    Ownership is stamped to the sandbox's non-root user: ``put_archive`` extracts as root and keeps
    whatever uid the tar carries, so the default (0) would make every file the control plane writes
    root-owned — and unwritable by the generated app's own toolchain, which runs as ``app``.
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo(name=name)
        info.size = len(data)
        info.mode = 0o644
        info.mtime = int(time.time())
        info.uid = SANDBOX_UID
        info.gid = SANDBOX_GID
        info.uname = SANDBOX_USER
        info.gname = SANDBOX_USER
        tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def read_single_from_tar(chunks: Any) -> bytes:
    """Extract the first regular-file member from a tar byte-stream (docker get_archive)."""
    buf = io.BytesIO()
    for chunk in chunks:
        buf.write(chunk)
    buf.seek(0)
    with tarfile.open(fileobj=buf, mode="r") as tar:
        for member in tar.getmembers():
            if member.isfile():
                extracted = tar.extractfile(member)
                return extracted.read() if extracted is not None else b""
    return b""


# --------------------------------------------------------------------------- LocalRuntime (tests)


def _kill_process_group(proc: subprocess.Popen[bytes]) -> None:
    """SIGKILL the whole group so children (shells, dev servers) die with the parent."""
    if proc.poll() is not None:
        return
    try:
        if not hasattr(os, "killpg"):  # pragma: no cover - the runtime target is Linux
            raise OSError("process groups are POSIX-only")
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass


class LocalExecHandle:
    def __init__(self, proc: subprocess.Popen[bytes]) -> None:
        self._proc = proc

    def stream(self) -> Iterator[ExecChunk]:
        sink: queue.Queue[ExecChunk | None] = queue.Queue()
        pipes: list[tuple[int, StreamName]] = []
        if self._proc.stdout is not None:
            pipes.append((self._proc.stdout.fileno(), "stdout"))
        if self._proc.stderr is not None:
            pipes.append((self._proc.stderr.fileno(), "stderr"))
        for fd, name in pipes:
            threading.Thread(target=_pump_pipe, args=(fd, name, sink), daemon=True).start()

        remaining = len(pipes)
        while remaining > 0:
            item = sink.get()
            if item is None:
                remaining -= 1
                continue
            yield item

    def wait(self) -> int:
        return self._proc.wait()

    def kill(self) -> None:
        _kill_process_group(self._proc)


class LocalTtyHandle:
    def __init__(self, proc: subprocess.Popen[bytes], master_fd: int) -> None:
        self._proc = proc
        self._master = master_fd
        self._closed = False

    def read(self, size: int = 4096) -> bytes:
        try:
            return os.read(self._master, size)
        except OSError:
            return b""  # EIO is how a PTY reports the child hung up

    def write(self, data: bytes) -> None:
        os.write(self._master, data)

    def resize(self, cols: int, rows: int) -> None:
        _set_winsize(self._master, cols, rows)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        _kill_process_group(self._proc)
        try:
            os.close(self._master)
        except OSError:
            pass
        self._proc.wait()  # reap; never leave a zombie behind


class LocalRuntime:
    """Host-directory backend for tests. Confines every op to ``root`` (defense in depth)."""

    def __init__(self, root: str) -> None:
        os.makedirs(root, exist_ok=True)
        self._root = os.path.realpath(root)

    @property
    def workspace_root(self) -> str:
        return self._root

    def _abs(self, rel: str) -> str:
        resolved = os.path.normpath(os.path.join(self._root, rel))
        if resolved != self._root and not resolved.startswith(self._root + os.sep):
            raise UserError("Path escapes workspace")
        return resolved

    def read_bytes(self, rel: str) -> bytes:
        with open(self._abs(rel), "rb") as handle:
            return handle.read()

    def write_bytes(self, rel: str, data: bytes) -> None:
        target = self._abs(rel)
        os.makedirs(os.path.dirname(target) or self._root, exist_ok=True)
        with open(target, "wb") as handle:
            handle.write(data)

    def delete(self, rel: str) -> None:
        target = self._abs(rel)
        if os.path.islink(target) or os.path.isfile(target):
            os.remove(target)
        elif os.path.isdir(target):
            shutil.rmtree(target)

    def mkdir(self, rel: str) -> None:
        os.makedirs(self._abs(rel), exist_ok=True)

    def move(self, src: str, dst: str) -> None:
        source = self._abs(src)
        target = self._abs(dst)
        if not os.path.lexists(source):
            raise FileNotFoundError(src)
        os.makedirs(os.path.dirname(target) or self._root, exist_ok=True)
        shutil.move(source, target)

    def size(self, rel: str) -> int:
        return os.path.getsize(self._abs(rel))

    def exists(self, rel: str) -> bool:
        return os.path.lexists(self._abs(rel))

    def real_path(self, rel: str) -> str:
        return os.path.realpath(self._abs(rel))

    def list_tree(self, start: str, depth: int) -> list[NodeInfo]:
        base = self._abs(start)
        if not os.path.isdir(base):
            return []
        base_depth = base.rstrip(os.sep).count(os.sep)
        nodes: list[NodeInfo] = []
        for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
            dirnames[:] = [d for d in dirnames if d not in _PRUNE_DIRS]
            cur_depth = dirpath.rstrip(os.sep).count(os.sep) - base_depth
            if cur_depth >= depth:
                dirnames[:] = []
            for name in dirnames:
                nodes.append(NodeInfo(self._rel(os.path.join(dirpath, name)), True, 0))
            for name in filenames:
                full = os.path.join(dirpath, name)
                try:
                    file_size = os.path.getsize(full)
                except OSError:
                    file_size = 0
                nodes.append(NodeInfo(self._rel(full), False, file_size))
        return nodes

    def _rel(self, abs_path: str) -> str:
        return os.path.relpath(abs_path, self._root).replace(os.sep, "/")

    def run_git(self, args: list[str], stdin: bytes | None = None) -> ExecResult:
        env = subprocess_env(
            {
                # Isolate from the host's user/system gitconfig (e.g. commit.gpgsign) for
                # determinism.
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_SYSTEM": os.devnull,
                "GIT_TERMINAL_PROMPT": "0",
            }
        )
        completed = subprocess.run(  # noqa: S603 - fixed argv (git), no shell
            ["git", *args],
            cwd=self._root,
            input=stdin,
            capture_output=True,
            env=env,
            check=False,
        )
        return ExecResult(completed.returncode, completed.stdout, completed.stderr)

    def start_exec(
        self, argv: list[str], cwd: str = "", env: dict[str, str] | None = None
    ) -> ExecHandle:
        proc = subprocess.Popen(  # noqa: S603 - argv list, never a shell string
            argv,
            cwd=self._abs(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            env=subprocess_env(env),
            start_new_session=True,  # own process group → killable as a unit
        )
        return LocalExecHandle(proc)

    def start_tty(
        self, argv: list[str], cwd: str = "", cols: int = 80, rows: int = 24
    ) -> TtyHandle:
        if pty is None:
            raise SystemError("Local interactive terminals are not supported on Windows")
        master, slave = pty.openpty()
        _set_winsize(master, cols, rows)
        try:
            proc = subprocess.Popen(  # noqa: S603 - argv list, never a shell string
                argv,
                cwd=self._abs(cwd),
                stdin=slave,
                stdout=slave,
                stderr=slave,
                env=subprocess_env({"TERM": "xterm-256color"}),
                start_new_session=True,
            )
        except BaseException:
            os.close(master)
            os.close(slave)
            raise
        os.close(slave)  # the child owns it now; keeping it open would mask EOF
        return LocalTtyHandle(proc, master)


# ------------------------------------------------------------------------- DockerRuntime (prod)


class DockerExecHandle:
    """A ``docker exec`` in flight. Killed by signalling its in-container PID."""

    def __init__(self, container: Any, exec_id: str, stream: Any) -> None:
        self._container = container
        self._api = container.client.api
        self._exec_id = exec_id
        self._stream = stream

    def stream(self) -> Iterator[ExecChunk]:
        for out, err in self._stream:
            if out:
                yield ExecChunk("stdout", out)
            if err:
                yield ExecChunk("stderr", err)

    def wait(self) -> int:
        # ExitCode is null until the exec finishes (drain the stream first) *and* stays null when
        # the container died mid-exec. Distinguish the two: a real 0 is success, but a null ExitCode
        # is EXIT_UNKNOWN, never 0 — else every `exit_code == 0` caller reads a dead container as a
        # passing command (phase-55 task 4).
        code = self._api.exec_inspect(self._exec_id).get("ExitCode")
        return EXIT_UNKNOWN if code is None else int(code)

    def kill(self) -> None:
        pid = self._api.exec_inspect(self._exec_id).get("Pid")
        if not pid:
            return
        # Docker has no "kill exec" API; signal the process group from inside the container.
        self._container.exec_run(["sh", "-c", f"kill -9 -{pid} 2>/dev/null || kill -9 {pid}"])


class DockerTtyHandle:
    def __init__(self, container: Any, exec_id: str, socket: Any) -> None:
        self._container = container
        self._api = container.client.api
        self._exec_id = exec_id
        self._socket = socket
        self._closed = False

    def _raw(self) -> Any:
        # docker-py wraps the connection; the raw socket is what supports recv/sendall.
        return getattr(self._socket, "_sock", self._socket)

    def read(self, size: int = 4096) -> bytes:
        try:
            data: bytes = self._raw().recv(size)
        except OSError:
            return b""
        return data

    def write(self, data: bytes) -> None:
        self._raw().sendall(data)

    def resize(self, cols: int, rows: int) -> None:
        self._api.exec_resize(self._exec_id, height=rows, width=cols)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        pid = self._api.exec_inspect(self._exec_id).get("Pid")
        if pid:
            self._container.exec_run(["sh", "-c", f"kill -9 -{pid} 2>/dev/null || kill -9 {pid}"])
        try:
            self._raw().close()
        except OSError:
            pass


class DockerRuntime:
    """Container backend: exec + archive APIs only, so untrusted files stay off the host."""

    def __init__(self, container: Any) -> None:
        self._container = container

    @property
    def workspace_root(self) -> str:
        return DOCKER_WORKSPACE

    def _abs(self, rel: str) -> str:
        resolved = posixpath.normpath(posixpath.join(DOCKER_WORKSPACE, rel))
        if resolved != DOCKER_WORKSPACE and not resolved.startswith(DOCKER_WORKSPACE + "/"):
            raise UserError("Path escapes workspace")
        return resolved

    def _exec(self, argv: list[str], workdir: str | None = None) -> ExecResult:
        exit_code, output = self._container.exec_run(argv, workdir=workdir, demux=True)
        stdout, stderr = output if output is not None else (None, None)
        return ExecResult(int(exit_code or 0), stdout or b"", stderr or b"")

    def read_bytes(self, rel: str) -> bytes:
        abs_path = self._abs(rel)
        try:
            bits, _ = self._container.get_archive(abs_path)
        except NotFound as exc:
            raise FileNotFoundError(rel) from exc
        return read_single_from_tar(bits)

    def write_bytes(self, rel: str, data: bytes) -> None:
        abs_path = self._abs(rel)
        parent = posixpath.dirname(abs_path)
        self._exec(["mkdir", "-p", "--", parent])
        ok = self._container.put_archive(parent, make_tar(posixpath.basename(abs_path), data))
        if not ok:
            raise SystemError("Failed to write file to sandbox")

    def delete(self, rel: str) -> None:
        self._exec(["rm", "-rf", "--", self._abs(rel)])

    def mkdir(self, rel: str) -> None:
        self._exec(["mkdir", "-p", "--", self._abs(rel)])

    def move(self, src: str, dst: str) -> None:
        source = self._abs(src)
        target = self._abs(dst)
        self._exec(["mkdir", "-p", "--", posixpath.dirname(target)])
        result = self._exec(["mv", "-f", "--", source, target])
        if not result.ok:
            if not self.exists(src):
                raise FileNotFoundError(src)
            raise SystemError("Failed to move file in sandbox")

    def size(self, rel: str) -> int:
        result = self._exec(["stat", "-c", "%s", "--", self._abs(rel)])
        if not result.ok:
            raise FileNotFoundError(rel)
        return int(result.stdout.decode().strip() or 0)

    def exists(self, rel: str) -> bool:
        return self._exec(["test", "-e", self._abs(rel)]).ok

    def real_path(self, rel: str) -> str:
        result = self._exec(["readlink", "-m", "--", self._abs(rel)])
        return result.stdout.decode().strip() or self._abs(rel)

    def list_tree(self, start: str, depth: int) -> list[NodeInfo]:
        start_arg = start or "."
        argv = [
            "find",
            start_arg,
            "-maxdepth",
            str(depth),
            "(",
            "-name",
            "node_modules",
            "-o",
            "-name",
            ".git",
            "-o",
            "-name",
            ".forgeflow",
            ")",
            "-prune",
            "-o",
            "-printf",
            "%y\t%s\t%p\n",
        ]
        result = self._exec(argv, workdir=DOCKER_WORKSPACE)
        nodes: list[NodeInfo] = []
        for line in result.stdout.decode("utf-8", errors="replace").splitlines():
            fields = line.split("\t")
            if len(fields) != 3:
                continue
            kind, raw_size, raw_path = fields
            path = raw_path[2:] if raw_path.startswith("./") else raw_path
            if not path or path == start_arg or path in _PRUNE_DIRS:
                continue
            is_dir = kind == "d"
            try:
                node_size = 0 if is_dir else int(raw_size)
            except ValueError:
                node_size = 0
            nodes.append(NodeInfo(path, is_dir, node_size))
        return nodes

    def run_git(self, args: list[str], stdin: bytes | None = None) -> ExecResult:
        # stdin-carrying git ops (e.g. `git apply`) arrive in the repair phases; not needed here.
        return self._exec(["git", *args], workdir=DOCKER_WORKSPACE)

    def start_exec(
        self, argv: list[str], cwd: str = "", env: dict[str, str] | None = None
    ) -> ExecHandle:
        api = self._container.client.api
        created = api.exec_create(
            self._container.id,
            argv,
            workdir=self._abs(cwd),
            environment=env or None,
            stdout=True,
            stderr=True,
            tty=False,
        )
        stream = api.exec_start(created["Id"], stream=True, demux=True)
        return DockerExecHandle(self._container, created["Id"], stream)

    def start_tty(
        self, argv: list[str], cwd: str = "", cols: int = 80, rows: int = 24
    ) -> TtyHandle:
        api = self._container.client.api
        created = api.exec_create(
            self._container.id,
            argv,
            workdir=self._abs(cwd),
            environment={"TERM": "xterm-256color"},
            stdout=True,
            stderr=True,
            stdin=True,
            tty=True,
        )
        socket = api.exec_start(created["Id"], tty=True, socket=True, demux=False)
        handle = DockerTtyHandle(self._container, created["Id"], socket)
        handle.resize(cols, rows)
        return handle
