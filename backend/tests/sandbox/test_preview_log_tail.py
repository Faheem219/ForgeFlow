"""A preview process keeps a bounded tail of its output as boot-failure evidence (phase-55 task 3).

The buffer is capped at ``preview_log_tail_lines`` and returns the most recent lines, so a crashed
dev server still leaves something to diagnose (and repair) from rather than nothing. No DB, no
sandbox — ``log_tail`` reads an in-memory ring and only ``str(project.id)`` off the project.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, cast

import pytest

from app.core.config import reset_config
from app.db.models import Project
from app.sandbox.preview import PreviewService, _new_log_tail, _Proc, _ProjectPreview
from app.sandbox.schemas import PreviewProcess


class _StubProject:
    """Stands in for a Project: log_tail only reads str(project.id)."""

    def __init__(self, pid: str) -> None:
        self.id = pid


@pytest.fixture
def small_tail(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("PREVIEW_LOG_TAIL_LINES", "3")
    reset_config()
    yield
    reset_config()


def _proc_with_lines(lines: list[str]) -> _Proc:
    tail = _new_log_tail()
    for line in lines:
        tail.append(line)
    return _Proc(handle=cast(Any, object()), port=3001, health_path="/health", log_tail=tail)


@pytest.mark.usefixtures("small_tail")
def test_log_tail_is_bounded_and_keeps_the_most_recent_lines() -> None:
    proc = _proc_with_lines([f"line {i}" for i in range(5)])
    assert len(proc.log_tail) == 3  # capped at PREVIEW_LOG_TAIL_LINES

    service = PreviewService()
    project = cast(Project, _StubProject("pid-123"))
    service._state["pid-123"] = _ProjectPreview(procs={PreviewProcess.backend: proc})

    tail = service.log_tail(project, PreviewProcess.backend)
    assert tail == "line 2\nline 3\nline 4"  # the last three, in order


@pytest.mark.usefixtures("small_tail")
def test_log_tail_is_empty_for_an_untracked_process() -> None:
    service = PreviewService()
    project = cast(Project, _StubProject("pid-456"))
    # Nothing started → no state → empty (not an error).
    assert service.log_tail(project, PreviewProcess.frontend) == ""
