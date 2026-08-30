"""Export (phase-45): CSV and markdown carry a **stable** schema and truthful values.

An export whose columns move between runs is useless for comparing results a month apart, so the
column order is asserted literally here — if someone reorders `CSV_COLUMNS`, this test is meant to
stop them. The other half is honesty: a missing measurement exports as an empty cell, never a `0`
that a spreadsheet would happily average.
"""

from __future__ import annotations

import csv
import io
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.api.app import create_app
from app.eval.report import CSV_COLUMNS, summarize, to_csv, to_markdown
from tests.eval.test_aggregation import record


def _rows(csv_text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(csv_text)))


# --------------------------------------------------------------------- CSV


def test_the_csv_schema_is_frozen() -> None:
    """Append-only. Reordering or renaming breaks every downstream analysis."""
    assert CSV_COLUMNS == (
        "spec_id",
        "title",
        "difficulty",
        "outcome",
        "first_pass_total",
        "first_pass_passed",
        "first_pass_rate",
        "post_repair_total",
        "post_repair_passed",
        "post_repair_rate",
        "repair_delta",
        "repair_iterations",
        "repair_outcome",
        "regressions",
        "tokens",
        "inr_cost",
        "screenshot_to_url_seconds",
        "wall_clock_seconds",
        "live_url",
        "error",
    )


def test_the_csv_header_matches_the_schema() -> None:
    header = to_csv([record("a")]).splitlines()[0]
    assert header.split(",") == list(CSV_COLUMNS)


def test_csv_values_match_the_underlying_record() -> None:
    rows = _rows(to_csv([record("todo", first=(4, 1), post=(4, 4), iterations=3, tokens=1500)]))

    assert len(rows) == 1
    row = rows[0]
    assert row["spec_id"] == "todo"
    assert row["first_pass_total"] == "4" and row["first_pass_passed"] == "1"
    assert row["first_pass_rate"] == "0.25" and row["post_repair_rate"] == "1.0"
    assert row["repair_delta"] == "0.75"
    assert row["repair_iterations"] == "3" and row["tokens"] == "1500"


def test_a_missing_measurement_exports_as_empty_never_zero() -> None:
    """A spreadsheet would average a 0 — an empty cell is skipped, which is the truth."""
    rows = _rows(to_csv([record("broken", first=None, post=None, ttu=None)]))

    assert rows[0]["first_pass_rate"] == ""
    assert rows[0]["post_repair_rate"] == ""
    assert rows[0]["repair_delta"] == ""
    assert rows[0]["screenshot_to_url_seconds"] == ""


def test_one_csv_row_per_spec() -> None:
    rows = _rows(to_csv([record("a"), record("b"), record("c")]))
    assert [r["spec_id"] for r in rows] == ["a", "b", "c"]


def test_an_empty_run_still_exports_a_usable_header() -> None:
    text = to_csv([])
    assert text.splitlines()[0].split(",") == list(CSV_COLUMNS)
    assert _rows(text) == []


def test_titles_containing_commas_are_quoted_properly() -> None:
    rows = _rows(to_csv([{**record("a"), "title": "Notes, with search"}]))
    assert rows[0]["title"] == "Notes, with search"


# --------------------------------------------------------------------- markdown


def test_markdown_leads_with_the_headline_comparison() -> None:
    records = [record("a", first=(4, 1), post=(4, 4)), record("b", first=(4, 3), post=(4, 4))]

    md = to_markdown(records)

    assert md.startswith("# ForgeFlow evaluation")
    assert "First-pass pass rate" in md and "Post-repair pass rate" in md
    assert "| 50% | 50% |" in md  # mean and median of 25% and 75%
    assert "Repair delta" in md


def test_markdown_shows_a_signed_delta() -> None:
    md = to_markdown([record("a", first=(4, 1), post=(4, 4))])
    assert "+75%" in md


def test_markdown_breaks_results_down_by_difficulty() -> None:
    records = [
        record("easy", difficulty="simple", first=(4, 4), post=(4, 4)),
        record("hard", difficulty="complex", first=(4, 1), post=(4, 3)),
    ]

    md = to_markdown(records)

    assert "## By difficulty" in md
    assert "| simple | 1 | 100% | 100% |" in md
    assert "| complex | 1 | 25% | 75% |" in md


def test_markdown_lists_every_spec() -> None:
    md = to_markdown([record("alpha"), record("beta")])
    assert "| alpha |" in md and "| beta |" in md


def test_markdown_renders_a_missing_measurement_as_a_dash() -> None:
    md = to_markdown([record("broken", first=None, post=None)])
    assert "—" in md
    assert "0%" not in md.split("## Per spec")[0].replace("100%", "")  # no fabricated zero


def test_markdown_and_csv_agree_with_the_summary() -> None:
    """The three views are the same numbers — a reader must never see them disagree."""
    records = [record("a", first=(4, 1), post=(4, 4)), record("b", first=(2, 1), post=(2, 2))]
    summary = summarize(records)

    csv_rows = _rows(to_csv(records))
    md = to_markdown(records, summary)

    assert float(csv_rows[0]["first_pass_rate"]) == records[0]["first_pass"]["rate"]
    assert summary["first_pass"]["mean"] == 0.375
    assert "| 38% | 38% |" in md  # the same mean, rendered


# --------------------------------------------------------------------- the API


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


pytestmark = pytest.mark.usefixtures("mongo_db")


async def _auth(client: AsyncClient, email: str) -> dict[str, str]:
    reg = await client.post("/auth/register", json={"email": email, "password": "password123"})
    return {"Authorization": f"Bearer {reg.json()['access_token']}"}


async def test_the_summary_endpoint_reports_unavailable_before_any_run(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import app.eval.report as report_module

    monkeypatch.setattr(report_module, "RESULTS_DIR", tmp_path)
    headers = await _auth(client, "noruns@eval.test")

    body = (await client.get("/eval/summary", headers=headers)).json()

    assert body["available"] is False
    assert body["records"] == []


async def test_the_eval_endpoints_require_authentication(client: AsyncClient) -> None:
    for path in ("/eval/summary", "/eval/runs", "/eval/report.csv", "/eval/report.md"):
        resp = await client.get(path)
        assert resp.status_code == 401, path


async def test_the_csv_endpoint_serves_a_download(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import json as json_lib

    import app.eval.report as report_module

    (tmp_path / "eval-1.json").write_text(
        json_lib.dumps({"records": [record("todo")], "legs": ["build"]}), encoding="utf-8"
    )
    monkeypatch.setattr(report_module, "RESULTS_DIR", tmp_path)
    headers = await _auth(client, "csv@eval.test")

    resp = await client.get("/eval/report.csv", headers=headers)

    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]
    assert "attachment" in resp.headers["content-disposition"]
    assert resp.text.splitlines()[0].split(",") == list(CSV_COLUMNS)


async def test_the_markdown_endpoint_serves_a_report(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import json as json_lib

    import app.eval.report as report_module

    (tmp_path / "eval-1.json").write_text(
        json_lib.dumps({"records": [record("todo", first=(4, 1), post=(4, 4))]}), encoding="utf-8"
    )
    monkeypatch.setattr(report_module, "RESULTS_DIR", tmp_path)
    headers = await _auth(client, "md@eval.test")

    resp = await client.get("/eval/report.md", headers=headers)

    assert resp.status_code == 200
    assert resp.text.startswith("# ForgeFlow evaluation")
    assert "+75%" in resp.text


async def test_runs_are_listed_newest_first(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import json as json_lib

    import app.eval.report as report_module

    for name in ("eval-100.json", "eval-200.json"):
        (tmp_path / name).write_text(
            json_lib.dumps({"records": [record("a")], "legs": ["build", "test"]}), encoding="utf-8"
        )
    monkeypatch.setattr(report_module, "RESULTS_DIR", tmp_path)
    headers = await _auth(client, "runs@eval.test")

    body = (await client.get("/eval/runs", headers=headers)).json()

    assert [r["source"] for r in body] == ["eval-200.json", "eval-100.json"]
    assert body[0]["specs"] == 1 and body[0]["legs"] == ["build", "test"]
