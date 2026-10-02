"""Run the industry benchmarks and render their results.

Writes ``eval/results/bench/bench-<timestamp>.json`` — every item's verdict plus each suite's
summary, model, settings, tokens and ₹ — so a number in a report can always be traced back to
the run that produced it.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.agents.models import TaskKind, route
from app.core.config import get_config
from app.db.models.common import utcnow
from app.eval.bench.bfcl import run_bfcl_suite
from app.eval.bench.code import assemble, run_code_suite
from app.eval.bench.datasets import (
    BFCL_CATEGORIES,
    CodeProblem,
    load_bfcl,
    load_humaneval,
    load_humaneval_ts,
    load_mbpp,
    reference_solutions,
)
from app.eval.bench.executor import BENCH_IMAGE, BenchSandbox, ExecResult
from app.eval.bench.llm import BenchLLM, Spend
from app.eval.runner import RESULTS_DIR

BENCH_RESULTS_DIR = RESULTS_DIR / "bench"

CODE_SUITES: dict[str, tuple[str, Callable[[], list[CodeProblem]]]] = {
    "humaneval": ("HumanEval (Python)", load_humaneval),
    "mbpp": ("MBPP sanitized (Python)", load_mbpp),
    "humaneval-ts": ("HumanEval-TS (MultiPL-E)", load_humaneval_ts),
}
SUITES: tuple[str, ...] = (*CODE_SUITES, "bfcl")


class _Progress:
    """One line per suite every ``every`` items — enough to see a long run is alive."""

    def __init__(self, totals: dict[str, int], every: int = 25) -> None:
        self._totals = totals
        self._every = every
        self._done: dict[str, int] = {}
        self._ok: dict[str, int] = {}

    def __call__(self, suite: str, item: Any) -> None:
        self._done[suite] = self._done.get(suite, 0) + 1
        ok = item.get("valid") if isinstance(item, dict) else item.first_pass
        self._ok[suite] = self._ok.get(suite, 0) + int(bool(ok))
        done, total = self._done[suite], self._totals.get(suite, 0)
        if done % self._every == 0 or done == total:
            label = "accuracy" if suite == "bfcl" else "pass@1"
            print(
                f"  [{suite}] {done}/{total} · {label} so far {self._ok[suite] / done:.1%}",
                flush=True,
            )


class GraderError(RuntimeError):
    """The grader failed its own check; no score it produced would mean anything."""


#: MultiPL-E ships no reference solutions, so a few hand-written ones stand in as positive controls:
#: they prove the TS path (extraction, assembly, ``tsc``, ``node``) can pass at all — a check the
#: always-wrong stubs alone cannot make.
TS_CONTROLS: dict[str, str] = {
    "HumanEval_0_has_close_elements": (
        "function has_close_elements(numbers: number[], threshold: number): boolean {\n"
        "  for (let i = 0; i < numbers.length; i++)\n"
        "    for (let j = i + 1; j < numbers.length; j++)\n"
        "      if (Math.abs(numbers[i] - numbers[j]) < threshold) return true;\n"
        "  return false;\n}"
    ),
    "HumanEval_2_truncate_number": (
        "function truncate_number(number: number): number {\n  return number % 1.0;\n}"
    ),
    "HumanEval_7_filter_by_substring": (
        "function filter_by_substring(strings: string[], substring: string): string[] {\n"
        "  return strings.filter((s) => s.includes(substring));\n}"
    ),
    "HumanEval_23_strlen": "function strlen(string: string): number {\n  return string.length;\n}",
    "HumanEval_53_add": "function add(x: number, y: number): number {\n  return x + y;\n}",
}


def _stub(problem: CodeProblem) -> str:
    if problem.language == "typescript":
        return f"function {problem.entry_point}(...args: any[]): any {{ return undefined; }}"
    return f"def {problem.entry_point}(*args, **kwargs):\n    return None\n"


async def verify_grader(sandbox: BenchSandbox) -> dict[str, Any]:
    """Every reference solution must pass, and an always-wrong stub must fail.

    References are upstream's for HumanEval and MBPP, and :data:`TS_CONTROLS` for HumanEval-TS.

    Runs the full problem sets regardless of ``--limit`` (it costs no tokens, only ~1-2 minutes of
    execution), so a reported pass rate is known to come from a grader that accepts correct code
    and rejects incorrect code.
    """
    references = {**reference_solutions(), **TS_CONTROLS}
    checks: list[tuple[CodeProblem, str, bool]] = []
    for _title, loader in CODE_SUITES.values():
        for problem in loader():
            if problem.id in references:
                checks.append((problem, references[problem.id], True))
            checks.append((problem, _stub(problem), False))

    async def run(problem: CodeProblem, code: str) -> ExecResult:
        return await sandbox.run(problem.language, assemble(problem, code))

    results = await asyncio.gather(*(run(p, code) for p, code, _ in checks))
    refs = [(p, r) for (p, _c, want), r in zip(checks, results, strict=True) if want]
    stubs = [(p, r) for (p, _c, want), r in zip(checks, results, strict=True) if not want]
    return {
        "reference_passed": sum(1 for _p, r in refs if r.passed),
        "reference_total": len(refs),
        "reference_failures": [p.id for p, r in refs if not r.passed],
        "stubs_rejected": sum(1 for _p, r in stubs if not r.passed),
        "stubs_total": len(stubs),
        "stubs_accepted": [p.id for p, r in stubs if r.passed],
    }


def _endpoint() -> str:
    config = get_config()
    provider = str(config.get("llm_provider")) or "anthropic"
    base = str(config.get("openai_base_url" if provider == "openai" else "anthropic_base_url"))
    pinned = str(config.get("openrouter_provider_order")).strip()
    upstream = f" · upstream pinned to {pinned}" if pinned and "openrouter" in base else ""
    return f"{provider} · {base or 'provider default'}{upstream}"


async def run_benchmarks(
    suites: list[str],
    *,
    limit: int | None = None,
    budget_inr: float | None = None,
    image: str = BENCH_IMAGE,
) -> dict[str, Any]:
    unknown = [s for s in suites if s not in SUITES]
    if unknown:
        raise ValueError(f"unknown suite(s) {unknown}; choose from {', '.join(SUITES)}")

    total = Spend(budget_inr=budget_inr)
    llm = BenchLLM(role=TaskKind.codegen)
    started = time.monotonic()
    report: dict[str, Any] = {
        "kind": "bench",
        "started_at": utcnow().isoformat(),
        "model": route(TaskKind.codegen),
        "endpoint": _endpoint(),
        "limit": limit,
        "suites": {},
    }

    code = [s for s in suites if s in CODE_SUITES]
    loaded = {s: CODE_SUITES[s][1]()[:limit] if limit else CODE_SUITES[s][1]() for s in code}
    cases = []
    if "bfcl" in suites:
        for category in BFCL_CATEGORIES:
            rows = load_bfcl(category)
            cases += rows[:limit] if limit else rows
    progress = _Progress({**{s: len(p) for s, p in loaded.items()}, "bfcl": len(cases)})

    if code:
        async with BenchSandbox(image) as sandbox:
            print("Verifying the grader against reference solutions…", flush=True)
            grader = await verify_grader(sandbox)
            report["grader_check"] = grader
            print(
                f"  reference solutions passing: {grader['reference_passed']}/"
                f"{grader['reference_total']} · always-wrong stubs rejected: "
                f"{grader['stubs_rejected']}/{grader['stubs_total']}",
                flush=True,
            )
            if grader["reference_failures"]:
                raise GraderError(
                    "reference solutions fail in the bench container: "
                    + ", ".join(grader["reference_failures"][:10])
                )
            for suite in code:
                print(f"\n{CODE_SUITES[suite][0]}: {len(loaded[suite])} problems", flush=True)
                spend = Spend(parent=total)
                summary = await run_code_suite(
                    suite, loaded[suite], llm=llm, executor=sandbox, spend=spend, progress=progress
                )
                summary["title"] = CODE_SUITES[suite][0]
                report["suites"][suite] = summary
    if cases:
        print(f"\nBFCL v3 non-live (Python): {len(cases)} cases", flush=True)
        spend = Spend(parent=total)
        summary = await run_bfcl_suite(cases, llm=llm, spend=spend, progress=progress)
        summary["title"] = "BFCL v3 non-live (Python)"
        report["suites"]["bfcl"] = summary

    report["finished_at"] = utcnow().isoformat()
    report["seconds"] = round(time.monotonic() - started, 1)
    report["spend"] = total.to_dict()
    report["budget_exhausted"] = total.exhausted
    return report


def write_bench(report: dict[str, Any], out: Path | None = None) -> Path:
    out = out or (BENCH_RESULTS_DIR / f"bench-{int(time.time())}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return out


def latest_bench(results_dir: Path | None = None) -> tuple[dict[str, Any], Path] | None:
    directory = results_dir or BENCH_RESULTS_DIR
    for path in sorted(directory.glob("bench-*.json"), reverse=True):
        try:
            return json.loads(path.read_text(encoding="utf-8")), path
        except (ValueError, OSError):
            continue
    return None


# --------------------------------------------------------------------- rendering


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def _pp(first: float | None, final: float | None) -> str:
    if first is None or final is None:
        return "—"
    return f"{(final - first) * 100:+.1f} pp"


def bench_markdown(report: dict[str, Any], *, level: int = 2) -> str:
    h = "#" * level
    suites = report.get("suites", {})
    code = [s for s in CODE_SUITES if s in suites]
    settings: dict[str, Any] = next((suites[s].get("settings", {}) for s in code), {})
    lines = [
        f"{h} Industry benchmarks",
        "",
        f"Model `{report.get('model')}` through ForgeFlow's own LLM transport "
        f"({report.get('endpoint')}). Run {str(report.get('started_at', ''))[:16]} UTC"
        + (f", **first {report['limit']} items per suite only**" if report.get("limit") else "")
        + ".",
        "",
    ]
    if code:
        grader = report.get("grader_check")
        lines += [
            f"{h}# Code generation — pass@1, then with ForgeFlow's bounded repair",
            "",
            "| Benchmark | Problems | pass@1 | + bounded repair | Gain | Repaired | "
            "Tokens | Cost (₹) |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for suite in code:
            s = suites[suite]
            lines.append(
                f"| {s.get('title', suite)} | {s['problems']} | **{_pct(s['pass_at_1'])}** | "
                f"{_pct(s['pass_after_repair'])} | {_pp(s['pass_at_1'], s['pass_after_repair'])} | "
                f"{s['repaired']} | {s['spend']['tokens']:,} | {s['spend']['inr']:.2f} |"
            )
        lines += [
            "",
            f"pass@1 = one greedy sample (temperature 0), graded by the benchmark's own tests — "
            "the leaderboard-comparable number. *+ bounded repair* feeds the failing test output "
            f"back for at most {settings.get('repair_max_iterations', '?')} attempts, stopping "
            f"after {settings.get('repair_stall_threshold', '?')} without progress (ForgeFlow's "
            "`REPAIR_MAX_ITERATIONS` / `REPAIR_STALL_THRESHOLD`); it is a self-repair number, "
            "not a pass@1. Code runs in a network-less, capability-dropped container.",
            "",
        ]
        if grader:
            lines += [
                f"Grader check (no model involved): {grader['reference_passed']}/"
                f"{grader['reference_total']} reference solutions pass (upstream's for "
                f"HumanEval/MBPP, {len(TS_CONTROLS)} hand-written TS controls) and "
                f"{grader['stubs_rejected']}/{grader['stubs_total']} always-wrong stubs are "
                "rejected.",
                "",
            ]
    bfcl = suites.get("bfcl")
    if bfcl:
        lines += [
            f"{h}# Tool calling — BFCL v3 (non-live, Python), official AST checker",
            "",
            "| Category | Cases | Accuracy |",
            "|---|---|---|",
        ]
        for category, row in bfcl["categories"].items():
            if category != "irrelevance":
                lines.append(f"| {category} | {row['cases']} | {_pct(row['accuracy'])} |")
        lines.append(f"| **Non-live AST mean** | — | **{_pct(bfcl['non_live_ast_mean'])}** |")
        irrelevance = bfcl["categories"].get("irrelevance")
        if irrelevance:
            lines.append(
                f"| irrelevance (correctly calls nothing) | {irrelevance['cases']} | "
                f"{_pct(irrelevance['accuracy'])} |"
            )
        lines += [
            "",
            f"{bfcl['spend']['tokens']:,} tokens · ₹{bfcl['spend']['inr']:.2f} · "
            f"{bfcl['provider_errors']} provider errors. Mean = unweighted mean of the four AST "
            "categories (not the leaderboard's *Overall*, which also includes Java/JS, live and "
            "multi-turn).",
            "",
        ]
    spend = report.get("spend", {})
    lines += [
        f"Total: {spend.get('calls', 0):,} model calls · {spend.get('tokens', 0):,} tokens · "
        f"₹{spend.get('inr', 0):.2f} · {report.get('seconds', 0) / 60:.0f} min.",
        "",
    ]
    if report.get("budget_exhausted"):
        lines += ["> ⚠️ The ₹ budget ran out before every item ran — results are partial.", ""]
    return "\n".join(lines)


__all__ = [
    "BENCH_RESULTS_DIR",
    "GraderError",
    "SUITES",
    "bench_markdown",
    "latest_bench",
    "run_benchmarks",
    "verify_grader",
    "write_bench",
]
