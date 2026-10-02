"""``python -m app.eval`` — every benchmark, one command (``make eval``).

1. **Industry benchmarks** (:mod:`app.eval.bench`): HumanEval, MBPP, HumanEval-TS, BFCL.
2. **End-to-end app generation** (:mod:`app.eval.runner`): the graded spec corpus through the
   real pipeline — build → testgen → test → bounded repair.
3. ``eval/results/REPORT.md``: the latest result of each part, with how it was produced.

Either part can be skipped (``--bench none`` / ``--apps none``); the report always combines the
newest result of each, so a long run can be done in pieces. Deploy is never run from here.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from app.core.config import MISSING, get_config
from app.eval.runner import RESULTS_DIR

REPORT_PATH = RESULTS_DIR / "REPORT.md"
#: ₹ ceiling per generated app, applied only when BUDGET_CAP_INR_PER_PROJECT is unset.
DEFAULT_APP_BUDGET_INR = 200.0
DEFAULT_BENCH_BUDGET_INR = 150.0


class _EvalOverrides:
    """Highest-priority config for this process only — nothing is written to `.env` or the DB."""

    name = "eval"

    def __init__(self, values: dict[str, Any]) -> None:
        self._values = values

    def get(self, key: str) -> Any:
        return self._values.get(key, MISSING)


def _git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
        )
        dirty = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
        return out.stdout.strip() + ("+dirty" if dirty.stdout.strip() else "")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m app.eval", description="Run every benchmark.")
    parser.add_argument(
        "--bench",
        default="all",
        help="industry suites: all | none | comma list of humaneval,mbpp,humaneval-ts,bfcl",
    )
    parser.add_argument("--apps", default="all", help="app specs: all | none | comma list of ids")
    parser.add_argument(
        "--limit", type=int, default=None, help="first N items per suite (a cheap smoke run)"
    )
    parser.add_argument("--bench-budget-inr", type=float, default=DEFAULT_BENCH_BUDGET_INR)
    parser.add_argument("--app-budget-inr", type=float, default=DEFAULT_APP_BUDGET_INR)
    parser.add_argument(
        "--jobs", type=int, default=3, help="apps built in parallel, one process each (default 3)"
    )
    parser.add_argument("--report-only", action="store_true", help="only rebuild REPORT.md")
    # Internal: how a parallel run's child processes hand their result back.
    parser.add_argument("--out", type=Path, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--no-report", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


async def _run_apps(args: argparse.Namespace) -> Path:
    from app.db.mongo import init_db
    from app.eval.runner import EvalRunner, Legs, format_report, select_specs, write_report

    specs = select_specs(args.apps)
    legs = Legs()
    print(f"\nEnd-to-end apps: {len(specs)} spec(s) · legs: {', '.join(legs.names())}", flush=True)
    if args.jobs > 1 and len(specs) > 1:
        report = await _run_apps_parallel([s.id for s in specs], args)
    else:
        await init_db()
        report = await EvalRunner().run_all(specs, legs=legs)
    print(format_report(report))
    return write_report(report, args.out)


async def _run_apps_parallel(spec_ids: list[str], args: argparse.Namespace) -> Any:
    """Each spec in its own process — own sandbox, own clock, own costed runs — ``--jobs`` at a
    time, merged into one report in corpus order. A child that dies is a failed record, not a
    silently missing one."""
    from app.eval.metrics import OUTCOME_FAILED, EvalReport, SpecMetrics

    workdir = Path(tempfile.mkdtemp(prefix="forgeflow-eval-"))
    gate = asyncio.Semaphore(args.jobs)
    print(f"Running {args.jobs} at a time · per-spec logs in {workdir}", flush=True)

    async def one(spec_id: str) -> dict[str, Any]:
        out, log = workdir / f"{spec_id}.json", workdir / f"{spec_id}.log"
        async with gate:
            print(f"  started  {spec_id}", flush=True)
            with log.open("wb") as handle:
                child = await asyncio.create_subprocess_exec(
                    sys.executable, "-m", "app.eval", "--bench", "none", "--apps", spec_id,
                    "--jobs", "1", "--app-budget-inr", str(args.app_budget_inr),
                    "--out", str(out), "--no-report",
                    stdout=handle, stderr=asyncio.subprocess.STDOUT,
                )  # fmt: skip
                code = await child.wait()
        if out.is_file():
            part: dict[str, Any] = json.loads(out.read_text(encoding="utf-8"))
            outcome = (part.get("records") or [{}])[0].get("outcome", "?")
            print(f"  finished {spec_id}: {outcome}", flush=True)
            return part
        print(f"  finished {spec_id}: process exited {code} without a result", flush=True)
        failed = SpecMetrics(spec_id=spec_id, outcome=OUTCOME_FAILED)
        failed.error = f"eval process exited {code}; see {log}"
        return {"records": [failed.to_dict()]}

    parts = await asyncio.gather(*(one(spec_id) for spec_id in spec_ids))
    return EvalReport.merge(list(parts))


def _latest_apps() -> tuple[list[dict[str, Any]], dict[str, Any]] | None:
    from app.eval.report import load_reports

    reports = load_reports()
    return (list(reports[0]["records"]), reports[0]) if reports else None


def build_report() -> str:
    from app.eval.bench.run import bench_markdown, latest_bench
    from app.eval.report import to_markdown

    config = get_config()
    lines = [
        "# ForgeFlow — benchmark report",
        "",
        f"Reproduce with `make eval` (see DEVELOPER.md). Code at `{_git_sha()}`.",
        "",
    ]
    bench = latest_bench()
    if bench is not None:
        data, path = bench
        lines += [bench_markdown(data), f"<sub>Source: `eval/results/bench/{path.name}`</sub>", ""]
    else:
        lines += ["## Industry benchmarks", "", "_Not run yet._", ""]

    apps = _latest_apps()
    if apps is not None:
        from app.eval.specs_loader import spec_paths

        records, meta = apps
        corpus = len(spec_paths())
        partial = (
            [
                f"> **Partial run: {len(records)} of {corpus} specs.** Do not read it as a corpus "
                "result.",
                "",
            ]
            if len(records) < corpus
            else []
        )
        lines += [
            *partial,
            to_markdown(records, level=2, title="End-to-end app generation (spec corpus)"),
            f"Model `{config.get('model_codegen')}` (codegen) / `{config.get('model_routing')}` "
            "(routing). *Criteria met* = share of each spec's acceptance criteria verified by a "
            "passing test the product's test-gen agent wrote from the spec; untested criteria "
            "count as unmet. *Tests* = every test in the workspace, skeleton included.",
            "",
            f"<sub>Source: `eval/results/{meta.get('source')}` · legs: "
            f"{', '.join(meta.get('legs') or [])}</sub>",
            "",
        ]
    else:
        lines += ["## End-to-end app generation", "", "_Not run yet._", ""]
    return "\n".join(lines)


async def _main(args: argparse.Namespace) -> int:
    overrides: dict[str, Any] = {}
    if get_config().get("budget_cap_inr_per_project") is None:
        overrides["budget_cap_inr_per_project"] = args.app_budget_inr
    get_config().add_provider(_EvalOverrides(overrides))

    if not args.report_only:
        if args.bench != "none":
            from app.eval.bench.run import SUITES, run_benchmarks, write_bench

            suites = list(SUITES) if args.bench == "all" else args.bench.split(",")
            report = await run_benchmarks(
                [s.strip() for s in suites if s.strip()],
                limit=args.limit,
                budget_inr=args.bench_budget_inr,
            )
            print(f"\nWrote {write_bench(report)}")
        if args.apps != "none":
            print(f"Wrote {await _run_apps(args)}")

    if args.no_report:
        return 0
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(build_report(), encoding="utf-8")
    print(f"\nReport: {REPORT_PATH}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_main(_parse(argv)))


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
