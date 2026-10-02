"""Hardened execution of model-written benchmark code (golden rule 3: never on the host).

One container per benchmark run, started from ``forgeflow-bench`` (``eval/bench/Dockerfile``) with
**no network**, every capability dropped, ``no-new-privileges``, a read-only root, a non-root user
and CPU/memory/pid limits. Programs are written into a private directory on a size-capped tmpfs and
run under a hard ``timeout``; one hung or hostile program cannot affect the next.

Commands follow the reference harnesses: ``python3 main.py`` (HumanEval/MBPP), and MultiPL-E's
``tsc --target esnext`` then ``node`` (a compile error is a failure, as there).
"""

from __future__ import annotations

import asyncio
import base64
import itertools
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

BENCH_IMAGE = "forgeflow-bench:latest"

#: Seconds a program may run. HumanEval's reference harness uses 3s; a shared, CPU-capped container
#: running several programs at once needs headroom, and a correct solution is never slower for it.
RUN_TIMEOUT_S = 10
#: `tsc` start-up alone is ~1-2s; compile time says nothing about correctness, so it gets slack.
COMPILE_TIMEOUT_S = 40
#: Characters of program output kept (the tail — where the assertion and traceback are).
OUTPUT_TAIL = 3000

_PYTHON_SCRIPT = (
    'set -e; d="/work/$1"; mkdir -p "$d"; cd "$d"; printf %s "$2" | base64 -d > main.py; set +e; '
    f"timeout -s KILL {RUN_TIMEOUT_S} python3 main.py"
)
_TS_SCRIPT = (
    'set -e; d="/work/$1"; mkdir -p "$d"; cd "$d"; printf %s "$2" | base64 -d > main.ts; set +e; '
    f"timeout -s KILL {COMPILE_TIMEOUT_S} tsc --target esnext main.ts || exit 3; "
    f"timeout -s KILL {RUN_TIMEOUT_S} node main.js"
)


@dataclass(frozen=True)
class ExecResult:
    passed: bool
    exit_code: int
    output: str

    @property
    def timed_out(self) -> bool:
        return self.exit_code in (124, 137)  # `timeout` (TERM) / SIGKILL


class BenchSandbox:
    """A long-lived, locked-down container that benchmark programs are executed in."""

    def __init__(
        self,
        image: str = BENCH_IMAGE,
        *,
        concurrency: int = 6,
        mem_limit: str = "3g",
        cpus: float = 4.0,
        pids_limit: int = 512,
    ) -> None:
        self._image = image
        self._mem_limit = mem_limit
        self._cpus = cpus
        self._pids_limit = pids_limit
        self._slots = asyncio.Semaphore(concurrency)
        self._ids = itertools.count()
        self._container: Any = None

    async def __aenter__(self) -> BenchSandbox:
        await asyncio.to_thread(self._start)
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await asyncio.to_thread(self._stop)

    def _start(self) -> None:
        import docker

        client = docker.from_env()
        try:
            client.images.get(self._image)
        except docker.errors.ImageNotFound as exc:
            raise RuntimeError(
                f"benchmark image {self._image!r} is missing — run `make bench-image`"
            ) from exc
        self._container = client.containers.run(
            self._image,
            ["sleep", "infinity"],
            detach=True,
            network_mode="none",
            cap_drop=["ALL"],
            security_opt=["no-new-privileges"],
            read_only=True,
            tmpfs={
                "/work": "rw,exec,size=512m,uid=1000,gid=1000,mode=0700",
                "/tmp": "rw,exec,size=128m,uid=1000,gid=1000,mode=0700",
            },
            mem_limit=self._mem_limit,
            nano_cpus=int(self._cpus * 1e9),
            pids_limit=self._pids_limit,
            user="1000:1000",
            labels={"forgeflow.bench": "true"},
        )

    def _stop(self) -> None:
        if self._container is None:
            return
        try:
            self._container.remove(force=True)
        except Exception:  # cleanup must never mask the run's own result
            logger.warning("bench: could not remove container", exc_info=True)
        self._container = None

    async def run(self, language: str, source: str) -> ExecResult:
        """Execute one program; it passes iff it exits 0 (every assertion held)."""
        if self._container is None:
            raise RuntimeError("BenchSandbox is not started")
        script = _TS_SCRIPT if language == "typescript" else _PYTHON_SCRIPT
        payload = base64.b64encode(source.encode()).decode()
        workdir = f"p{next(self._ids)}"
        async with self._slots:
            exit_code, output = await asyncio.to_thread(self._exec, script, workdir, payload)
            await asyncio.to_thread(self._exec, 'rm -rf "/work/$1"', workdir, "")
        text = output.decode(errors="replace")[-OUTPUT_TAIL:]
        if exit_code == 3 and language == "typescript":
            text = "TypeScript compile error:\n" + text
        return ExecResult(passed=exit_code == 0, exit_code=exit_code, output=text)

    def _exec(self, script: str, workdir: str, payload: str) -> tuple[int, bytes]:
        result = self._container.exec_run(
            ["sh", "-c", script, "sh", workdir, payload], user="1000:1000", workdir="/work"
        )
        return int(result.exit_code if result.exit_code is not None else -1), result.output or b""


__all__ = ["BENCH_IMAGE", "BenchSandbox", "ExecResult"]
