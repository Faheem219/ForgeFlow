"""Code benchmarks: extraction, assembly and the bounded repair policy (no model, no Docker)."""

from __future__ import annotations

from typing import Any

import pytest

from app.eval.bench.code import (
    STOP_BUDGET,
    STOP_FIRST,
    STOP_MAX_ITERATIONS,
    STOP_NO_PROGRESS,
    STOP_REPAIRED,
    assemble,
    extract_code,
    solve,
)
from app.eval.bench.datasets import CodeProblem
from app.eval.bench.executor import ExecResult
from app.eval.bench.llm import Reply, Spend

HUMANEVAL = CodeProblem(
    id="HumanEval/x",
    language="python",
    prompt='def add(a, b):\n    """Add."""\n',
    entry_point="add",
    preamble='def add(a, b):\n    """Add."""\n',
    tests="def check(f):\n    assert f(1, 2) == 3\n\ncheck(add)\n",
)
MBPP = CodeProblem(
    id="Mbpp/1",
    language="python",
    prompt="Add.\nassert add(1, 2) == 3",
    entry_point="add",
    tests="assert add(1, 2) == 3\n",
    header="import math",
)
TS = CodeProblem(
    id="HumanEval_0_add",
    language="typescript",
    prompt="function add(a: number, b: number): number {\n",
    entry_point="add",
    preamble="function add(a: number, b: number): number {\n",
    tests="add(1, 2);\n",
)


def test_the_block_defining_the_entry_point_wins_over_longer_prose_blocks() -> None:
    reply = (
        "Here:\n```python\nprint('example usage that is much longer than the answer')\n```\n"
        "```python\ndef add(a, b):\n    return a + b\n```"
    )
    assert extract_code(reply, HUMANEVAL) == "def add(a, b):\n    return a + b"


def test_an_unfenced_reply_is_used_as_is() -> None:
    assert extract_code("def add(a, b):\n    return a + b\n", MBPP).startswith("def add")


def test_typescript_export_is_stripped_so_the_script_runs_under_node() -> None:
    code = extract_code("```ts\nexport function add(a: number, b: number) { return a+b }\n```", TS)
    assert code.startswith("function add")


def test_a_full_python_definition_follows_the_stub_and_a_body_completes_it() -> None:
    full = assemble(HUMANEVAL, "def add(a, b):\n    return a + b")
    body = assemble(HUMANEVAL, "    return a + b\n")

    assert full.index('"""Add."""') < full.index("return a + b") < full.index("check(add)")
    assert body.startswith(HUMANEVAL.preamble + "    return a + b")


def test_mbpp_puts_test_imports_first() -> None:
    assert assemble(MBPP, "def add(a, b):\n    return a + b").startswith("import math\n")


def test_a_full_typescript_function_replaces_the_open_stub() -> None:
    program = assemble(TS, "function add(a: number, b: number): number { return a + b; }")
    assert program.count("function add") == 1


# --------------------------------------------------------------------- the repair policy


class ScriptedLLM:
    model = "test-model"

    def __init__(self, codes: list[str]) -> None:
        self._codes = list(codes)
        self.prompts: list[str] = []

    async def complete(self, *, messages: list[dict[str, Any]], spend: Spend, **_: Any) -> Reply:
        self.prompts.append(messages[-1]["content"])
        spend.calls += 1
        return Reply(text=f"```python\n{self._codes.pop(0)}\n```")


class Grader:
    """Passes code containing 'good'; fails everything else with the given message."""

    def __init__(self, failure: str = "AssertionError") -> None:
        self.failure = failure
        self.runs = 0

    async def run(self, language: str, source: str) -> ExecResult:
        self.runs += 1
        if "good" in source:
            return ExecResult(passed=True, exit_code=0, output="")
        return ExecResult(passed=False, exit_code=1, output=f"Traceback\n{self.failure}")


async def _solve(llm: ScriptedLLM, grader: Grader, spend: Spend | None = None) -> Any:
    return await solve(
        HUMANEVAL,
        llm=llm,  # type: ignore[arg-type]
        executor=grader,
        spend=spend or Spend(),
        max_iterations=5,
        stall_threshold=2,
    )


@pytest.mark.asyncio
async def test_a_first_pass_solution_is_not_sent_to_repair() -> None:
    llm = ScriptedLLM(["def add(a, b):\n    return a + b  # good"])
    result = await _solve(llm, Grader())

    assert (result.first_pass, result.passed, result.iterations, result.stop) == (
        True,
        True,
        0,
        STOP_FIRST,
    )


@pytest.mark.asyncio
async def test_repair_feeds_the_failure_back_and_counts_iterations() -> None:
    llm = ScriptedLLM(
        ["def add(a, b):\n    return a - b", "def add(a, b):\n    return a + b  # good"]
    )
    grader = Grader(failure="AssertionError: assert 1 == 3")
    result = await _solve(llm, grader)

    assert (result.first_pass, result.passed, result.iterations, result.stop) == (
        False,
        True,
        1,
        STOP_REPAIRED,
    )
    # Minimal context: the task, the failing code and its output — not a growing transcript.
    assert "return a - b" in llm.prompts[1] and "assert 1 == 3" in llm.prompts[1]


@pytest.mark.asyncio
async def test_resubmitting_the_same_code_is_no_progress_and_is_not_re_executed() -> None:
    same = "def add(a, b):\n    return 0"
    grader = Grader()
    result = await _solve(ScriptedLLM([same, same, same]), grader)

    assert result.stop == STOP_NO_PROGRESS and result.iterations == 2
    assert grader.runs == 1


@pytest.mark.asyncio
async def test_the_loop_is_bounded_by_max_iterations() -> None:
    codes = [f"def add(a, b):\n    return {i}" for i in range(6)]

    class Changing(Grader):
        async def run(self, language: str, source: str) -> ExecResult:
            self.failure = f"AssertionError {self.runs}"  # a new failure every time → no stall
            return await super().run(language, source)

    result = await _solve(ScriptedLLM(codes), Changing())

    assert result.stop == STOP_MAX_ITERATIONS and result.iterations == 5 and not result.passed


@pytest.mark.asyncio
async def test_an_exhausted_budget_stops_repair() -> None:
    result = await _solve(
        ScriptedLLM(["def add(a, b):\n    return 0"]), Grader(), Spend(budget_inr=0.0)
    )
    assert result.stop == STOP_BUDGET and result.iterations == 0


def test_a_suite_spend_also_counts_toward_and_is_bound_by_the_run_total() -> None:
    from app.agents.cost import Usage

    total = Spend(budget_inr=0.0)
    suite = Spend(parent=total)
    suite.add("unpriced-model", Usage(10, 5))

    assert (total.calls, total.input_tokens, total.output_tokens) == (1, 10, 5)
    assert suite.exhausted


@pytest.mark.asyncio
async def test_a_failed_model_call_is_recorded_against_the_item_not_raised() -> None:
    import httpx

    class Broken(ScriptedLLM):
        async def complete(self, **_: Any) -> Reply:
            raise httpx.ReadTimeout("network gone")

    result = await _solve(Broken([]), Grader())

    assert result.stop == "provider_error" and not result.passed
    assert "ReadTimeout" in (result.error or "")
