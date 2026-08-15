"""A container that dies mid-exec must never read as a successful command (phase-55 task 4).

Docker reports ``ExitCode: null`` for an exec whose container died before it finished. The old
``int(... or 0)`` coerced that to 0 = success, so every ``exit_code == 0`` caller (deps, the test
runner, the preview health probe) silently treated a dead container as a pass. ``wait()`` now
returns :data:`EXIT_UNKNOWN` instead.
"""

from __future__ import annotations

from typing import Any

from app.sandbox.runtime import EXIT_UNKNOWN, DockerExecHandle


class _FakeApi:
    def __init__(self, inspect: dict[str, Any]) -> None:
        self._inspect = inspect

    def exec_inspect(self, _exec_id: str) -> dict[str, Any]:
        return self._inspect


class _FakeContainer:
    def __init__(self, inspect: dict[str, Any]) -> None:
        self.client = type("C", (), {"api": _FakeApi(inspect)})()


def _handle(inspect: dict[str, Any]) -> DockerExecHandle:
    return DockerExecHandle(_FakeContainer(inspect), "exec-1", iter(()))


def test_null_exit_code_is_exit_unknown_not_zero() -> None:
    # The dead-container case: ExitCode is null → EXIT_UNKNOWN, never 0.
    assert _handle({"ExitCode": None}).wait() == EXIT_UNKNOWN
    assert EXIT_UNKNOWN != 0


def test_real_exit_codes_pass_through() -> None:
    assert _handle({"ExitCode": 0}).wait() == 0
    assert _handle({"ExitCode": 1}).wait() == 1
    assert _handle({"ExitCode": 137}).wait() == 137  # OOM-kill still surfaces as itself
