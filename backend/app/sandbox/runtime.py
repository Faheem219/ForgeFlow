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
import shutil
import subprocess
import tarfile
import time
from dataclasses import dataclass
from typing import Any, Protocol

from docker.errors import NotFound

# SystemError shadows the builtin (taxonomy name fixed by the plan); A004 suppressed on-line.
from app.core.errors import SystemError, UserError  # noqa: A004

DOCKER_WORKSPACE = "/workspace"
_PRUNE_DIRS = {"node_modules", ".git"}


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


# --------------------------------------------------------------------------- tar helpers (pure)


def make_tar(name: str, data: bytes) -> bytes:
    """Build an in-memory tar carrying a single file member ``name`` → ``data``."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo(name=name)
        info.size = len(data)
        info.mode = 0o644
        info.mtime = int(time.time())
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
        env = {
            **os.environ,
            # Isolate from the host's user/system gitconfig (e.g. commit.gpgsign) for determinism.
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
        }
        completed = subprocess.run(  # noqa: S603 - fixed argv (git), no shell
            ["git", *args],
            cwd=self._root,
            input=stdin,
            capture_output=True,
            env=env,
            check=False,
        )
        return ExecResult(completed.returncode, completed.stdout, completed.stderr)


# ------------------------------------------------------------------------- DockerRuntime (prod)


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
