"""PID exhaustion and held ports are ENVIRONMENT failures, not code (phase-59).

Both were reported from one live session. Neither is fixable by patching source, so both must
short-circuit before the repair loop spends a model iteration on them — the phase-55 contract.
"""

from __future__ import annotations

from app.agents.build_errors import BuildFailureKind, classify_output

# The exact abort node produces when it cannot start its libuv threadpool at the PID ceiling. It
# names neither PIDs nor limits, which is precisely why it needs an explicit signature.
_UV_THREAD_ABORT = """
  #  node[10982]: std::unique_ptr<long unsigned int> node::WorkerThreadsTaskRunner::\
DelayedTaskScheduler::Start() at ../src/node_platform.cc:68
  #  Assertion failed: (0) == (uv_thread_create(t.get(), start_thread, this))
----- Native stack trace -----
 1: 0xc978fc node::Assert(node::AssertionInfo const&) [node]
"""

_EADDRINUSE = """
Error: listen EADDRINUSE: address already in use 0.0.0.0:3001
    at Server.setupListenHandle [as _listen2] (node:net:1908:16)
  code: 'EADDRINUSE',
"""

_VITE_PORT_SLIDE = """
Port 5173 is in use, trying another one...
Port 5174 is in use, trying another one...
  ➜  Local:   http://localhost:5175/
"""


def test_the_uv_thread_create_abort_is_an_environment_failure() -> None:
    diagnosis = classify_output(_UV_THREAD_ABORT, exit_code=134)

    assert diagnosis.kind is BuildFailureKind.environment
    assert "process limit" in diagnosis.reason
    assert "SANDBOX_PIDS_LIMIT" in diagnosis.hint


def test_the_uv_thread_abort_does_not_reach_the_repair_loop() -> None:
    """`repairable` is what gates the loop; a native abort must not read as a patchable bug."""
    assert classify_output(_UV_THREAD_ABORT, exit_code=134).repairable is False


def test_eaddrinuse_is_an_environment_failure() -> None:
    diagnosis = classify_output(_EADDRINUSE, exit_code=1)

    assert diagnosis.kind is BuildFailureKind.environment
    assert "port" in diagnosis.reason


def test_vite_sliding_to_another_port_is_an_environment_failure() -> None:
    """The proxy routes to a fixed port, so a slide is a broken preview, not a working one."""
    diagnosis = classify_output(_VITE_PORT_SLIDE, exit_code=1)

    assert diagnosis.kind is BuildFailureKind.environment


def test_an_ordinary_compile_error_is_still_code() -> None:
    """Regression guard: the new signatures must not swallow real, patchable failures."""
    output = "src/app.ts(12,5): error TS2345: Argument of type 'string' is not assignable."

    assert classify_output(output, exit_code=2).repairable is True
