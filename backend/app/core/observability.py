"""Tracing & correlation (phase-46, §7/D4).

Phase-20 already accounts for spend on a :class:`~app.db.models.Run`; this module makes that work
*traceable* rather than adding a second accounting system. Two pieces:

**Correlation.** A run's ``project_id`` / ``run_id`` / ``kind`` live in context variables, and a
logging filter stamps them onto every record emitted while that context is active. So a log line
written deep inside a tool call can be tied back to the run that caused it without every call site
having to pass ids around — and a request that touches three subsystems reads as one story.

**One traced unit.** :func:`traced_run` opens a ``Run``, binds the correlation context, times the
work, records its outcome, and always closes it — including when the work raises, because an
un-finished ``Run`` is worse than none: it silently understates spend and leaves a trace that looks
like it is still going.

Redaction lives in ``app/core/logging.py`` and applies to everything here; nothing in this module
logs a value it did not receive as an id.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from beanie import PydanticObjectId

from app.db.models import Run
from app.db.models.common import utcnow
from app.db.repos import RunRepo

logger = logging.getLogger(__name__)

#: The correlation context. Empty outside a traced run — the filter then adds nothing.
_project_id: ContextVar[str | None] = ContextVar("forgeflow_project_id", default=None)
_run_id: ContextVar[str | None] = ContextVar("forgeflow_run_id", default=None)
_run_kind: ContextVar[str | None] = ContextVar("forgeflow_run_kind", default=None)

# Outcomes a traced run can end with when the caller does not set one explicitly.
OUTCOME_OK = "ok"
OUTCOME_ERROR = "error"


@dataclass(frozen=True)
class Correlation:
    project_id: str | None
    run_id: str | None
    kind: str | None

    def as_dict(self) -> dict[str, str]:
        return {
            key: value
            for key, value in (
                ("project_id", self.project_id),
                ("run_id", self.run_id),
                ("run_kind", self.kind),
            )
            if value
        }


def current_correlation() -> Correlation:
    """Whatever run this code is executing inside, if any."""
    return Correlation(_project_id.get(), _run_id.get(), _run_kind.get())


class CorrelationFilter(logging.Filter):
    """Stamp the active run's ids onto every record, so logs can be grouped by run.

    A filter rather than a formatter: it applies whatever handler or format is configured, and it
    never overwrites an id a call site set deliberately.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        for key, value in current_correlation().as_dict().items():
            if not hasattr(record, key):
                setattr(record, key, value)
        return True


def install_correlation_filter(target: logging.Logger | None = None) -> None:
    """Attach the filter to every handler on the root logger (idempotent)."""
    root = target or logging.getLogger()
    for handler in root.handlers:
        if not any(isinstance(f, CorrelationFilter) for f in handler.filters):
            handler.addFilter(CorrelationFilter())


@asynccontextmanager
async def traced_run(
    kind: str,
    *,
    project_id: PydanticObjectId | None = None,
    run: Run | None = None,
    runs: RunRepo | None = None,
) -> AsyncIterator[Run]:
    """Open (or adopt) a ``Run``, bind correlation, and always close it.

    ``run`` lets a caller that already created its own ``Run`` — the stage handlers do — get the
    correlation and the guaranteed close without a second record being written for the same work.
    """
    repo = runs or RunRepo()
    started = time.monotonic()
    record = run or await repo.insert(Run(project_id=project_id, kind=kind))

    tokens = (
        _project_id.set(str(project_id) if project_id else None),
        _run_id.set(str(record.id) if record.id else None),
        _run_kind.set(kind),
    )
    logger.info("run started", extra={"run_kind": kind})
    try:
        yield record
    except Exception:
        # An error is an outcome, and it must be recorded before the exception continues.
        await _close(record, OUTCOME_ERROR, started)
        logger.warning("run failed", extra={"run_kind": kind}, exc_info=True)
        raise
    else:
        await _close(record, record.outcome or OUTCOME_OK, started)
        logger.info(
            "run finished",
            extra={
                "run_kind": kind,
                "outcome": record.outcome,
                "tokens": record.cost.tokens,
                "inr": round(record.cost.inr, 4),
            },
        )
    finally:
        _project_id.reset(tokens[0])
        _run_id.reset(tokens[1])
        _run_kind.reset(tokens[2])


async def _close(run: Run, outcome: str, started: float) -> None:
    run.outcome = outcome
    if run.finished_at is None:
        run.finished_at = utcnow()
    try:
        await run.save()
    except Exception:  # losing the close must not mask the caller's own error
        logger.warning("could not finalise run", exc_info=True)
    _ = time.monotonic() - started  # duration is derivable from started/finished_at


def record_tool_call(
    run: Run, tool: str, *, ok: bool, detail: dict[str, Any] | None = None
) -> None:
    """Append one auditable tool call to the run's trail (phase-21's contract).

    In-memory only — the caller persists with the rest of its update, so a busy agent does not
    incur a database write per tool call.
    """
    entry: dict[str, Any] = {"tool": tool, "ok": ok, "at": utcnow().isoformat()}
    if detail:
        entry.update(detail)
    run.tool_calls.append(entry)


__all__ = [
    "OUTCOME_ERROR",
    "OUTCOME_OK",
    "Correlation",
    "CorrelationFilter",
    "current_correlation",
    "install_correlation_filter",
    "record_tool_call",
    "traced_run",
]
