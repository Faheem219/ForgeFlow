"""Command execution inside the sandbox (phase-13).

Runs a command in the project's container, streaming ``terminal.output`` chunks over the realtime
hub, bounded by a timeout, cancellable mid-flight, and capped per project. All execution happens
inside the sandbox as the non-root user (§7) — the control plane never execs generated code.

The blocking runtime handle is bridged to asyncio by pumping its generator on a worker thread into
an :class:`asyncio.Queue`, so streaming never blocks the event loop.

One exception to "the sandbox has no network": a command that must reach the npm registry (a
dependency install — the generated app's deps are not baked into the image) runs inside a bounded
egress window. See :mod:`app.sandbox.network` for the guarantees.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from dataclasses import dataclass

from app.core.config import get_config
from app.core.errors import NotFoundError, UserError
from app.db.blobs import BlobStore, get_blob_store
from app.db.models import Project
from app.realtime.hub import emit
from app.realtime.schemas import EventType
from app.sandbox.network import egress_enabled, egress_window, needs_registry
from app.sandbox.runtime import DockerRuntime, ExecChunk, ExecHandle, WorkspaceRuntime
from app.sandbox.workspace import RuntimeProvider, active_runtime_provider

logger = logging.getLogger(__name__)

# Conventional shell exit code for "killed by timeout" (matches coreutils `timeout`).
EXIT_TIMEOUT = 124


def wants_registry(runtime: WorkspaceRuntime, cmd: list[str]) -> bool:
    """Whether this command should run inside a registry-egress window.

    Three conditions, all required: it is a real container (the local test backend has no docker
    networking), the relaxation is enabled, and the command genuinely fetches packages. Everything
    else keeps the sandbox's sealed-network default.
    """
    return isinstance(runtime, DockerRuntime) and egress_enabled() and needs_registry(cmd)


@dataclass(frozen=True)
class ExecOutcome:
    exec_id: str
    exit_code: int
    duration_s: float
    timed_out: bool
    cancelled: bool
    output_ref: str | None = None


async def aiter_chunks(handle: ExecHandle) -> AsyncIterator[ExecChunk]:
    """Bridge the runtime's blocking chunk generator onto the event loop.

    Shared with the preview service (phase-15), which pumps long-lived dev-server output.
    """
    loop = asyncio.get_running_loop()
    sink: asyncio.Queue[ExecChunk | None] = asyncio.Queue()

    def post(item: ExecChunk | None) -> None:
        # After a timeout/cancel the consumer is gone and the loop may already be closed;
        # the pump thread must not blow up trying to hand off its final chunks.
        try:
            loop.call_soon_threadsafe(sink.put_nowait, item)
        except RuntimeError:
            pass

    def pump() -> None:
        try:
            for chunk in handle.stream():
                post(chunk)
        except Exception:  # a broken stream must not wedge the consumer
            logger.warning("exec stream pump failed", exc_info=True)
        finally:
            post(None)

    threading.Thread(target=pump, daemon=True).start()
    while True:
        item = await sink.get()
        if item is None:
            return
        yield item


class ExecService:
    """Owns running execs so they can be cancelled and capped per project."""

    def __init__(
        self,
        provider: RuntimeProvider | None = None,
        blob_store: BlobStore | None = None,
    ) -> None:
        self._provider = provider
        self._blob_store = blob_store
        self._running: dict[str, dict[str, ExecHandle]] = {}
        self._cancelled: set[str] = set()

    def running_count(self, project_id: str) -> int:
        return len(self._running.get(project_id, {}))

    async def cancel(self, project_id: str, exec_id: str) -> None:
        """Kill a running command. Idempotent for an already-finished exec."""
        handle = self._running.get(project_id, {}).get(exec_id)
        if handle is None:
            raise NotFoundError("No such running command")
        self._cancelled.add(exec_id)
        await asyncio.to_thread(handle.kill)

    async def run(
        self,
        project: Project,
        cmd: list[str],
        cwd: str = "",
        env: dict[str, str] | None = None,
        timeout: float | None = None,
        capture: bool = True,
    ) -> ExecOutcome:
        if not cmd:
            raise UserError("A command is required")

        project_id = str(project.id)
        config = get_config()
        limit = int(config.get("sandbox_max_concurrent_execs"))
        if self.running_count(project_id) >= limit:
            raise UserError(f"At most {limit} concurrent commands per project")

        window = timeout if timeout is not None else float(config.get("sandbox_exec_timeout_s"))
        provider = self._provider or active_runtime_provider()
        runtime = await provider(project)

        exec_id = uuid.uuid4().hex[:16]
        started = time.monotonic()
        collected = bytearray()
        timed_out = False

        async with AsyncExitStack() as stack:
            # A dependency install is the one thing generated code cannot do under the
            # sealed-network default — the registry is unreachable. Borrow routed egress for
            # exactly this command and hand it straight back (see app.sandbox.network).
            if wants_registry(runtime, cmd):
                await stack.enter_async_context(egress_window(project_id))

            handle = await asyncio.to_thread(runtime.start_exec, cmd, cwd, env)
            self._running.setdefault(project_id, {})[exec_id] = handle

            await emit(
                project_id,
                EventType.exec_status,
                {"exec_id": exec_id, "status": "started", "cmd": cmd},
            )

            try:
                async with asyncio.timeout(window):
                    async for chunk in aiter_chunks(handle):
                        collected += chunk.data
                        await emit(
                            project_id,
                            EventType.terminal_output,
                            {
                                "exec_id": exec_id,
                                "stream": chunk.stream,
                                "chunk": chunk.data.decode("utf-8", errors="replace"),
                            },
                        )
                    exit_code = await asyncio.to_thread(handle.wait)
            except TimeoutError:
                timed_out = True
                await asyncio.to_thread(handle.kill)
                # Reap so the process (and its group) is fully gone before we report back.
                await asyncio.to_thread(handle.wait)
                exit_code = EXIT_TIMEOUT
            finally:
                self._running.get(project_id, {}).pop(exec_id, None)

        cancelled = exec_id in self._cancelled
        self._cancelled.discard(exec_id)
        duration = time.monotonic() - started

        output_ref = await self._capture(bytes(collected)) if capture else None

        await emit(
            project_id,
            EventType.exec_status,
            {
                "exec_id": exec_id,
                "status": "exited",
                "exit_code": exit_code,
                "timed_out": timed_out,
                "cancelled": cancelled,
                "duration_s": round(duration, 3),
            },
        )
        return ExecOutcome(
            exec_id=exec_id,
            exit_code=exit_code,
            duration_s=duration,
            timed_out=timed_out,
            cancelled=cancelled,
            output_ref=output_ref,
        )

    async def _capture(self, data: bytes) -> str | None:
        """Persist combined output for later TestRun stdout refs (best-effort)."""
        if not data:
            return None
        store = self._blob_store or get_blob_store()
        try:
            return await store.put(data)
        except Exception:  # blob trouble must not invalidate an otherwise-good run
            logger.warning("failed to capture exec output to blob store", exc_info=True)
            return None


_service: ExecService | None = None


def get_exec_service() -> ExecService:
    global _service
    if _service is None:
        _service = ExecService()
    return _service


def set_exec_service(service: ExecService | None) -> None:
    global _service
    _service = service


def reset_exec_service() -> None:
    global _service
    _service = None
