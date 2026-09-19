"""Repair prompts (phase-30).

One prompt surface: a strict, Sonnet-routed **patch** step that sees *only* the minimal
:class:`~app.agents.repair_context.RepairContext` (phase-29) — the failing tests, the files they
exercise, the diff since the code last passed, and the acceptance criteria at stake.

The load-bearing rule encoded here is that **the tests are the specification**: the agent repairs
source, it never weakens the oracle. Test files are rendered read-only (the agent's writable set
excludes them), which is what keeps a "green" run meaningful. No I/O here — the agent owns wiring.
"""

from __future__ import annotations

from app.agents.repair_context import RepairContext

# System guidance for the patch step — routed to MODEL_CODEGEN (Sonnet).
REPAIR_SYSTEM_EXTRA = (
    "You are repairing a failing test run with the SMALLEST change that works. You are given only "
    "the failing tests, the files they exercise, the diff since the code last passed, and the "
    "acceptance criteria at stake — that limited context is deliberate, and it is all you get.\n"
    "Rules (strict):\n"
    "- Fix the failing BEHAVIOUR in the source. NEVER weaken, skip, delete, or rewrite a test to "
    "make it pass: the tests are the specification, and test files are read-only here.\n"
    "- Do NOT break tests that currently pass. Prefer the narrowest change that satisfies the "
    "acceptance criterion.\n"
    "- Touch as few files as possible, and only files listed as EDITABLE below. If the fix truly "
    "requires a file you were not given, do NOT guess its contents and do NOT give up: call "
    "`request_file` with a path you have actually seen (in an import statement of a file above, or "
    "in a stack) — it returns that file and makes it editable, within a small per-attempt budget. "
    "Only if that still leaves you stuck, say so plainly in your summary.\n"
    "- The diff since the last passing commit is the strongest clue: a regression usually lives "
    "there.\n"
    "- `write_file` takes the FULL new contents of the file, not a patch fragment.\n"
    "Finish with 2–3 sentences: what you changed, and why that fixes the failing behaviour."
)

_MAX_STACK_CHARS = 1200


def _format_failing_tests(context: RepairContext) -> str:
    blocks: list[str] = []
    for test in context.failing_tests:
        lines = [f"### {test.name}  ({test.framework})"]
        if test.criterion_id:
            lines.append(f"criterion: {test.criterion_id}")
        if test.file:
            lines.append(f"test file: {test.file}")
        lines.append(f"error: {test.message}")
        if test.assertion and test.assertion != test.name:
            lines.append(f"assertion: {test.assertion}")
        if test.stack:
            stack = test.stack[:_MAX_STACK_CHARS]
            lines.append(f"stack:\n{stack}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) or "(no failing tests)"


def _format_criteria(context: RepairContext) -> str:
    if not context.requirement_snippets:
        return "No acceptance criteria were recorded for these failures."
    return "\n".join(
        f"- [{s.criterion_id}] ({s.feature}) {s.text}" for s in context.requirement_snippets
    )


def _format_files(context: RepairContext, editable: set[str]) -> str:
    blocks: list[str] = []
    for file in context.target_files:
        tag = "EDITABLE" if file.path in editable else "READ-ONLY (test / oracle)"
        note = " [truncated]" if file.truncated else ""
        blocks.append(f"--- {file.path}  [{tag}]{note}\n{file.content}")
    return "\n\n".join(blocks) or "(no files supplied)"


def repair_user_prompt(
    context: RepairContext, editable: set[str], iteration: int, *, request_budget: int = 0
) -> str:
    """Render the minimal context into the patch request."""
    diff = context.diff or "(no diff available — repair from the failures alone)"
    editable_list = ", ".join(sorted(editable)) if editable else "(none)"
    hatch = (
        f"\nIf the fix belongs in a file that is not listed, call `request_file` with its path "
        f"(up to {request_budget} this attempt) — the imports in the files above name the paths "
        f"you can ask for."
        if request_budget > 0
        else ""
    )
    trimmed = (
        "\n\nNote — the context was trimmed to fit its budget:\n"
        + "\n".join(f"- {t}" for t in context.trimmed)
        if context.trimmed
        else ""
    )
    # phase-65: failures the sandbox's missing network explains, named up front so the agent fixes
    # the code rather than rediscovering the network rules one attempt at a time.
    sandbox = (
        "## Sandbox environment\n" + "\n".join(f"- {h}" for h in context.sandbox_hints) + "\n\n"
        if context.sandbox_hints
        else ""
    )
    return (
        f"Repair attempt #{iteration}.\n\n"
        f"## Failing tests\n{_format_failing_tests(context)}\n\n"
        f"{sandbox}"
        f"## Acceptance criteria at stake\n{_format_criteria(context)}\n\n"
        f"## Diff since the last passing commit\n{diff}\n\n"
        f"## Files\n{_format_files(context, editable)}\n\n"
        f"## Editable files\nYou may ONLY write to: {editable_list}{hatch}"
        f"{trimmed}\n\n"
        "Make the smallest change that turns the failing tests green without breaking the others."
    )
