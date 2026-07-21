"""Run tracing (phase-46): every unit of work attaches to a Run with cost and correlation.

The property that matters is that a run is *always* closed. An un-finished Run understates spend,
looks like it is still going, and quietly breaks the budget guard that sums those records — so the
failure path gets as much attention here as the happy one.
"""

from __future__ import annotations

import logging

import pytest
from beanie import PydanticObjectId

from app.agents.cost import Usage, record_cost
from app.core.observability import (
    OUTCOME_ERROR,
    OUTCOME_OK,
    CorrelationFilter,
    current_correlation,
    install_correlation_filter,
    record_tool_call,
    traced_run,
)
from app.db.models import Run

pytestmark = pytest.mark.usefixtures("mongo_db")


async def test_a_traced_run_is_created_timed_and_closed() -> None:
    project_id = PydanticObjectId()

    async with traced_run("codegen:build", project_id=project_id) as run:
        assert run.id is not None
        assert run.finished_at is None  # still open inside the block

    stored = await Run.get(run.id)
    assert stored is not None
    assert stored.kind == "codegen:build"
    assert stored.outcome == OUTCOME_OK
    assert stored.finished_at is not None
    assert stored.finished_at >= stored.started_at


async def test_a_failing_run_is_recorded_as_failed_and_still_closed() -> None:
    """The exception must propagate — but the trace must not be left dangling."""
    project_id = PydanticObjectId()

    with pytest.raises(RuntimeError, match="boom"):
        async with traced_run("repair:loop", project_id=project_id) as run:
            raise RuntimeError("boom")

    stored = await Run.get(run.id)
    assert stored is not None
    assert stored.outcome == OUTCOME_ERROR
    assert stored.finished_at is not None


async def test_an_explicit_outcome_survives_the_close() -> None:
    """The repair loop sets `fixed`/`escalated`; tracing must not overwrite it with `ok`."""
    async with traced_run("repair:loop", project_id=PydanticObjectId()) as run:
        run.outcome = "escalated"

    stored = await Run.get(run.id)
    assert stored is not None and stored.outcome == "escalated"


async def test_cost_accrues_onto_the_traced_run() -> None:
    """Spend and trace are one record — the design note's single source of truth."""
    async with traced_run("codegen:build", project_id=PydanticObjectId()) as run:
        await record_cost(run, "claude-sonnet-5", Usage(input_tokens=1000, output_tokens=500))

    stored = await Run.get(run.id)
    assert stored is not None
    assert stored.cost.tokens == 1500
    assert stored.cost.inr > 0


async def test_an_existing_run_is_adopted_rather_than_duplicated() -> None:
    """Stage handlers open their own Run; tracing must not write a second one for the same work."""
    project_id = PydanticObjectId()
    existing = await Run(project_id=project_id, kind="deploy").insert()

    async with traced_run("deploy", project_id=project_id, run=existing) as run:
        assert run.id == existing.id

    runs = await Run.find({"project_id": project_id}).to_list()
    assert len(runs) == 1
    assert runs[0].finished_at is not None


async def test_tool_calls_build_an_auditable_trail() -> None:
    async with traced_run("codegen:build", project_id=PydanticObjectId()) as run:
        record_tool_call(run, "write_file", ok=True, detail={"path": "src/app.ts"})
        record_tool_call(run, "run_command", ok=False)
        await run.save()

    stored = await Run.get(run.id)
    assert stored is not None
    assert [c["tool"] for c in stored.tool_calls] == ["write_file", "run_command"]
    assert stored.tool_calls[0]["ok"] is True and stored.tool_calls[1]["ok"] is False
    assert stored.tool_calls[0]["path"] == "src/app.ts"


# --------------------------------------------------------------------- correlation


async def test_correlation_is_bound_inside_the_run_and_cleared_after() -> None:
    project_id = PydanticObjectId()
    assert current_correlation().run_id is None

    async with traced_run("codegen:build", project_id=project_id) as run:
        inside = current_correlation()
        assert inside.project_id == str(project_id)
        assert inside.run_id == str(run.id)
        assert inside.kind == "codegen:build"

    assert current_correlation().run_id is None  # context restored


async def test_nested_runs_restore_the_outer_context() -> None:
    outer_project = PydanticObjectId()
    inner_project = PydanticObjectId()

    async with traced_run("conductor:build", project_id=outer_project):
        outer_id = current_correlation().run_id
        async with traced_run("codegen:build", project_id=inner_project):
            assert current_correlation().project_id == str(inner_project)
        # The inner run must not leave the outer trace pointing at itself.
        assert current_correlation().run_id == outer_id
        assert current_correlation().project_id == str(outer_project)


async def test_log_records_carry_the_run_correlation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A log line written deep inside a tool call must be traceable to its run."""
    logger = logging.getLogger("test.correlated")
    logger.addFilter(CorrelationFilter())
    project_id = PydanticObjectId()

    with caplog.at_level(logging.INFO, logger="test.correlated"):
        async with traced_run("codegen:build", project_id=project_id) as run:
            logger.info("deep inside a tool call")

    record = next(r for r in caplog.records if r.message == "deep inside a tool call")
    assert record.project_id == str(project_id)  # type: ignore[attr-defined]
    assert record.run_id == str(run.id)  # type: ignore[attr-defined]
    assert record.run_kind == "codegen:build"  # type: ignore[attr-defined]


def test_a_log_outside_any_run_gains_no_correlation(caplog: pytest.LogCaptureFixture) -> None:
    logger = logging.getLogger("test.uncorrelated")
    logger.addFilter(CorrelationFilter())

    with caplog.at_level(logging.INFO, logger="test.uncorrelated"):
        logger.info("no run here")

    record = caplog.records[-1]
    assert not hasattr(record, "run_id")


def test_installing_the_filter_twice_does_not_double_it() -> None:
    root = logging.getLogger("test.idempotent")
    root.addHandler(logging.NullHandler())

    install_correlation_filter(root)
    install_correlation_filter(root)

    filters = [f for h in root.handlers for f in h.filters if isinstance(f, CorrelationFilter)]
    assert len(filters) == 1
