"""Code benchmarks (HumanEval, MBPP, HumanEval-TS): pass@1, then pass@1 after bounded repair.

**pass@1** is the leaderboard number: one greedy sample (temperature 0), graded by the benchmark's
own tests, nothing else. It is reported first and on its own.

**pass@1 + repair** applies ForgeFlow's repair *policy* to each failure: the failing output goes
back to the model, at most ``REPAIR_MAX_ITERATIONS`` times, stopping early after
``REPAIR_STALL_THRESHOLD`` attempts in a row that make no progress (same failure, or unchanged
code). Each attempt sees only the task, the current code and the failure — the minimal context the
product's loop uses, not a growing transcript. Feedback comes from the benchmark's tests, as in the
self-repair literature (Chen et al., *Self-Debugging*, ICLR 2024; Olausson et al., *Is Self-Repair
a Silver Bullet?*, ICLR 2024) — in ForgeFlow the tests *are* the specification, so this is the same
contract. It is therefore a different number from pass@1 and is always labelled as such.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any, Protocol

from app.core.config import get_config
from app.eval.bench.datasets import CodeProblem
from app.eval.bench.executor import ExecResult
from app.eval.bench.llm import BenchLLM, Spend

STOP_FIRST = "passed_first"
STOP_REPAIRED = "repaired"
STOP_MAX_ITERATIONS = "max_iterations"
STOP_NO_PROGRESS = "no_progress"
STOP_BUDGET = "budget"
STOP_ERROR = "provider_error"

#: Characters of failing output handed back to the model per repair attempt.
FEEDBACK_CHARS = 1500

_LANG_TAG = {"python": "python", "typescript": "typescript"}
_SYSTEM = {
    "python": (
        "You are an expert Python programmer. Reply with the complete solution in a single "
        "```python code block and nothing else."
    ),
    "typescript": (
        "You are an expert TypeScript programmer. Reply with the complete solution in a single "
        "```typescript code block and nothing else. Do not use import or export statements."
    ),
}


class _Executor(Protocol):
    async def run(self, language: str, source: str) -> ExecResult: ...


@dataclass
class CodeResult:
    id: str
    first_pass: bool
    passed: bool
    iterations: int
    stop: str
    seconds: float
    error: str | None = None


# --------------------------------------------------------------------- prompting + assembly


def task_message(problem: CodeProblem) -> str:
    if problem.language == "typescript":
        return (
            "Complete the following TypeScript function. Return the whole function.\n\n"
            f"```typescript\n{problem.prompt}\n```"
        )
    if problem.preamble:  # HumanEval: a signature + docstring to complete
        return (
            "Complete the following Python function. Return the whole function, with any "
            f"imports it needs.\n\n```python\n{problem.prompt}\n```"
        )
    return f"{problem.prompt}\n\nWrite the Python function."  # MBPP: a description + one test


_FENCE = re.compile(r"```[ \t]*[A-Za-z0-9_+.-]*[ \t]*\n(.*?)```", re.S)
_OPEN_FENCE = re.compile(r"```[^\n]*\n(.*)$", re.S)
_TS_EXPORT = re.compile(r"^([ \t]*)export\s+(?:default\s+)?", re.M)


def defines_entry(code: str, problem: CodeProblem) -> bool:
    name = re.escape(problem.entry_point)
    if not name:
        return False
    if problem.language == "typescript":
        return bool(re.search(rf"function\s+{name}\s*[(<]|(?:const|let|var)\s+{name}\s*=", code))
    return bool(re.search(rf"^\s*def\s+{name}\s*\(", code, re.M))


def extract_code(text: str, problem: CodeProblem) -> str:
    """The solution from a reply: the fenced block defining the entry point, else the longest."""
    blocks: list[str] = [str(b) for b in _FENCE.findall(text)]
    if not blocks:
        unterminated = _OPEN_FENCE.search(text)
        blocks = [str(unterminated.group(1))] if unterminated else [text]
    defining = [b for b in blocks if defines_entry(b, problem)]
    code = defining[0] if defining else max(blocks, key=len)
    if problem.language == "typescript":
        # The grading script is not a module; `export` would make node reject the compiled file.
        code = _TS_EXPORT.sub(r"\1", code)
    return code.strip("\n")


def assemble(problem: CodeProblem, code: str) -> str:
    """The program the reference harness would run: [header] + solution + tests."""
    if not problem.preamble:
        body = code
    elif not defines_entry(code, problem):
        body = problem.preamble + code  # a bare body completes the stub, as in the reference
    elif problem.language == "python":
        # HumanEval's stub is a complete (docstring-only) function that may carry helpers the
        # solution relies on; the full definition that follows overrides it.
        body = f"{problem.preamble}\n\n{code}"
    else:
        body = code  # a TS stub ends mid-function, so a full definition replaces it
    parts = [problem.header, body, problem.tests]
    return "\n\n".join(part for part in parts if part)


def _describe(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:300]


def _signature(output: str) -> str:
    lines = [line.strip() for line in output.strip().splitlines() if line.strip()]
    return "\n".join(lines[-2:])


# --------------------------------------------------------------------- one problem


async def solve(
    problem: CodeProblem,
    *,
    llm: BenchLLM,
    executor: _Executor,
    spend: Spend,
    max_iterations: int,
    stall_threshold: int,
) -> CodeResult:
    started = time.monotonic()
    system = _SYSTEM[problem.language]
    tag = _LANG_TAG[problem.language]
    task = task_message(problem)

    def done(
        first: bool, passed: bool, iterations: int, stop: str, error: str | None = None
    ) -> CodeResult:
        seconds = round(time.monotonic() - started, 2)
        return CodeResult(problem.id, first, passed, iterations, stop, seconds, error)

    try:
        reply = await llm.complete(
            system=system, messages=[{"role": "user", "content": task}], spend=spend
        )
    except Exception as exc:  # a failed call is this item's result, never the whole run's
        return done(False, False, 0, STOP_ERROR, _describe(exc))
    code = extract_code(reply.text, problem)
    result = await executor.run(problem.language, assemble(problem, code))
    if result.passed:
        return done(True, True, 0, STOP_FIRST)

    iterations = 0
    stalls = 0
    seen = {code}
    last = _signature(result.output)
    while True:
        if iterations >= max_iterations:
            return done(False, False, iterations, STOP_MAX_ITERATIONS)
        if spend.exhausted:
            return done(False, False, iterations, STOP_BUDGET)
        iterations += 1
        feedback = (
            f"{task}\n\nYour previous solution:\n\n```{tag}\n{code}\n```\n\n"
            f"It failed the tests:\n\n```\n{result.output[-FEEDBACK_CHARS:]}\n```\n\n"
            f"Fix it. Reply with the complete corrected solution in one ```{tag} code block."
        )
        try:
            reply = await llm.complete(
                system=system, messages=[{"role": "user", "content": feedback}], spend=spend
            )
        except Exception as exc:
            return done(False, False, iterations, STOP_ERROR, _describe(exc))
        code = extract_code(reply.text, problem)
        if code in seen:
            stalls += 1  # resubmitting known code cannot change the verdict — don't execute it
        else:
            seen.add(code)
            result = await executor.run(problem.language, assemble(problem, code))
            if result.passed:
                return done(False, True, iterations, STOP_REPAIRED)
            signature = _signature(result.output)
            stalls = stalls + 1 if signature == last else 0
            last = signature
        if stalls >= stall_threshold:
            return done(False, False, iterations, STOP_NO_PROGRESS)


# --------------------------------------------------------------------- a suite


async def run_code_suite(
    name: str,
    problems: list[CodeProblem],
    *,
    llm: BenchLLM,
    executor: _Executor,
    spend: Spend,
    concurrency: int = 8,
    max_iterations: int | None = None,
    stall_threshold: int | None = None,
    progress: Any = None,
) -> dict[str, Any]:
    config = get_config()
    max_iterations = (
        int(config.get("repair_max_iterations")) if max_iterations is None else max_iterations
    )
    stall_threshold = (
        int(config.get("repair_stall_threshold")) if stall_threshold is None else stall_threshold
    )
    gate = asyncio.Semaphore(concurrency)
    started = time.monotonic()

    async def one(problem: CodeProblem) -> CodeResult:
        async with gate:
            if spend.exhausted:
                return CodeResult(problem.id, False, False, 0, STOP_BUDGET, 0.0)
            result = await solve(
                problem,
                llm=llm,
                executor=executor,
                spend=spend,
                max_iterations=max_iterations,
                stall_threshold=stall_threshold,
            )
            if progress is not None:
                progress(name, result)
            return result

    results = await asyncio.gather(*(one(p) for p in problems))
    return summarize_code(
        name,
        list(results),
        spend=spend,
        seconds=time.monotonic() - started,
        settings={
            "model": llm.model,
            "temperature": 0.0,
            "repair_max_iterations": max_iterations,
            "repair_stall_threshold": stall_threshold,
        },
    )


def summarize_code(
    name: str,
    results: list[CodeResult],
    *,
    spend: Spend,
    seconds: float,
    settings: dict[str, Any],
) -> dict[str, Any]:
    n = len(results)
    first = sum(1 for r in results if r.first_pass)
    final = sum(1 for r in results if r.passed)
    repaired = [r for r in results if r.stop == STOP_REPAIRED]
    return {
        "suite": name,
        "kind": "code",
        "problems": n,
        "pass_at_1": round(first / n, 4) if n else None,
        "pass_after_repair": round(final / n, 4) if n else None,
        "solved_first_pass": first,
        "solved_after_repair": final,
        "repaired": len(repaired),
        "mean_iterations_to_repair": (
            round(sum(r.iterations for r in repaired) / len(repaired), 2) if repaired else None
        ),
        "stops": dict(Counter(r.stop for r in results)),
        "provider_errors": sum(1 for r in results if r.stop == STOP_ERROR),
        "spend": spend.to_dict(),
        "seconds": round(seconds, 1),
        "settings": settings,
        "items": [asdict(r) for r in results],
    }


__all__ = [
    "CodeResult",
    "assemble",
    "defines_entry",
    "extract_code",
    "run_code_suite",
    "solve",
    "summarize_code",
    "task_message",
]
