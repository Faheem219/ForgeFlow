"""Preview lifecycle: start → healthy → restart → stop, plus failure and log streaming.

Driven against **real HTTP servers** via LocalRuntime, so health-polling, log streaming, restart
and teardown are genuinely exercised (the Docker backend runs the identical service logic inside
the container). Stand-in "FE"/"BE" servers replace the phase-22 skeleton, whose content is
explicitly out of scope here.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import socket
import sys
from collections.abc import AsyncIterator
from urllib.error import URLError

import pytest
from beanie import PydanticObjectId

from app.core.config import get_config, reset_config
from app.db.models import Project
from app.projects.service import ProjectService
from app.realtime.hub import get_hub
from app.sandbox.preview import PreviewService
from app.sandbox.runtime import LocalRuntime, WorkspaceRuntime
from app.sandbox.schemas import PreviewStatus
from tests.sandbox.conftest import requires_posix_runtime

pytestmark = [
    pytest.mark.usefixtures("mongo_db"),
    requires_posix_runtime,
    # The health probe is `curl` run through the runtime — present in the sandbox image and on CI
    # runners. Without it every server reads "starting" and assertions fail for the wrong reason.
    pytest.mark.skipif(shutil.which("curl") is None, reason="the preview health probe runs curl"),
]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


async def _project() -> Project:
    return await ProjectService().create_project(PydanticObjectId(), "p")


# A tiny server standing in for a dev server: serves / and /health, and logs to stdout.
_SERVER = (
    "import http.server, os, sys\n"
    "port = int(os.environ.get('PORT') or sys.argv[1])\n"
    "class H(http.server.BaseHTTPRequestHandler):\n"
    "    def do_GET(self):\n"
    "        self.send_response(200)\n"
    "        self.send_header('Content-Type', 'text/html')\n"
    "        self.end_headers()\n"
    "        self.wfile.write(b'<h1>preview up</h1>')\n"
    "    def log_message(self, *a): pass\n"
    "sys.stdout.write('LISTENING on %d\\n' % port); sys.stdout.flush()\n"
    "http.server.HTTPServer(('127.0.0.1', port), H).serve_forever()\n"
)


@pytest.fixture
async def preview(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[PreviewService]:
    root = os.path.join(str(tmp_path), "ws")
    os.makedirs(os.path.join(root, "frontend"), exist_ok=True)
    os.makedirs(os.path.join(root, "backend"), exist_ok=True)
    with open(os.path.join(root, "server.py"), "w") as handle:
        handle.write(_SERVER)

    fe_port, be_port = _free_port(), _free_port()
    # Both "dev servers" run the same stand-in; the FE takes its port on argv, the BE via $PORT.
    monkeypatch.setenv("PREVIEW_FE_CMD", f"{sys.executable} ../server.py {{port}}")
    monkeypatch.setenv("PREVIEW_BE_CMD", f"{sys.executable} ../server.py {be_port}")
    monkeypatch.setenv("PREVIEW_FE_PORT", str(fe_port))
    monkeypatch.setenv("PREVIEW_BE_PORT", str(be_port))
    monkeypatch.setenv("PREVIEW_HEALTH_TIMEOUT_S", "20")
    reset_config()

    async def provider(_project: Project) -> WorkspaceRuntime:
        return LocalRuntime(root)

    service = PreviewService(provider=provider)
    yield service

    # A test that fails before its own stop() would otherwise leave both dev servers running, and
    # their log pumps then block the event loop's teardown forever: the suite HANGS (a CI job sits
    # until its timeout) instead of reporting the failure. Stop whatever is still tracked.
    for state in list(service._state.values()):
        for proc in state.procs.values():
            with contextlib.suppress(Exception):
                await asyncio.to_thread(proc.handle.kill)
            if proc.task is not None:
                proc.task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await proc.task
    service._state.clear()


async def test_start_brings_both_servers_up_healthy(preview: PreviewService) -> None:
    project = await _project()
    info = await preview.start(project)

    assert info.fe_status == PreviewStatus.running
    assert info.be_status == PreviewStatus.running
    assert info.fe_url == f"http://{project.id}.preview.localhost"
    assert info.be_url == f"http://{project.id}.api.preview.localhost"

    await preview.stop(project)


async def test_status_is_stopped_before_start_and_after_stop(preview: PreviewService) -> None:
    project = await _project()

    before = await preview.status(project)
    assert before.fe_status == PreviewStatus.stopped
    assert before.be_status == PreviewStatus.stopped
    assert before.fe_url is None

    await preview.start(project)
    assert (await preview.status(project)).fe_status == PreviewStatus.running

    after = await preview.stop(project)
    assert after.fe_status == PreviewStatus.stopped
    assert (await preview.status(project)).fe_status == PreviewStatus.stopped


async def test_the_served_page_is_actually_reachable(preview: PreviewService) -> None:
    """The acceptance criterion behind the iframe: the FE really serves content."""
    project = await _project()
    await preview.start(project)
    try:
        fe_port = int(get_config().get("preview_fe_port"))
        be_port = int(get_config().get("preview_be_port"))
        body = await asyncio.to_thread(_http_get, fe_port)
        assert "preview up" in body
        # FE → BE: the backend is reachable too (in prod both ride the restricted network).
        assert "preview up" in await asyncio.to_thread(_http_get, be_port, "/health")
    finally:
        await preview.stop(project)


def _http_get(port: int, path: str = "/") -> str:
    import urllib.request

    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as resp:
        return str(resp.read().decode())


async def test_stop_kills_the_servers(preview: PreviewService) -> None:
    project = await _project()
    await preview.start(project)
    fe_port = int(get_config().get("preview_fe_port"))
    be_port = int(get_config().get("preview_be_port"))
    assert "preview up" in await asyncio.to_thread(_http_get, fe_port)  # really serving

    await preview.stop(project)
    await asyncio.sleep(0.3)

    # Nothing answers any more → the processes are really gone, not just forgotten.
    # (Asserting on port *binding* would be flaky: closed connections linger in TIME_WAIT.)
    for port in (fe_port, be_port):
        with pytest.raises(URLError):  # connection refused
            await asyncio.to_thread(_http_get, port)


async def test_restart_recovers_a_running_preview(preview: PreviewService) -> None:
    project = await _project()
    first = await preview.start(project)
    assert first.fe_status == PreviewStatus.running

    again = await preview.restart(project)
    assert again.fe_status == PreviewStatus.running
    assert again.be_status == PreviewStatus.running

    await preview.stop(project)


async def test_start_is_idempotent_and_does_not_double_run(preview: PreviewService) -> None:
    project = await _project()
    await preview.start(project)
    # A second start must not collide on the port (it stops the previous servers first).
    info = await preview.start(project)
    assert info.fe_status == PreviewStatus.running
    await preview.stop(project)


async def test_logs_stream_tagged_per_process(preview: PreviewService) -> None:
    project = await _project()
    await preview.start(project)
    try:
        events = [
            e
            for e in get_hub().replay(str(project.id), 0)
            if e.event == "terminal.output" and e.payload.get("source") == "preview"
        ]
        processes = {e.payload["process"] for e in events}
        assert processes == {"frontend", "backend"}
        assert any("LISTENING" in str(e.payload["chunk"]) for e in events)
    finally:
        await preview.stop(project)


async def test_emits_preview_status_events(preview: PreviewService) -> None:
    project = await _project()
    await preview.start(project)
    try:
        statuses = [e for e in get_hub().replay(str(project.id), 0) if e.event == "preview.status"]
        assert any(e.payload.get("status") == "starting" for e in statuses)
        assert statuses[-1].payload.get("fe_status") == "running"
    finally:
        await preview.stop(project)


async def test_a_crashing_server_is_reported_failed(
    preview: PreviewService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PREVIEW_BE_CMD", f"{sys.executable} -c 'import sys; sys.exit(1)'")
    monkeypatch.setenv("PREVIEW_HEALTH_TIMEOUT_S", "5")
    reset_config()

    project = await _project()
    info = await preview.start(project)

    # Fails fast rather than burning the whole health window.
    assert info.be_status == PreviewStatus.failed
    await preview.stop(project)
