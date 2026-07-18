"""Shared helpers for the test-generation agent tests (phase-27).

Not a test module (no ``test_`` prefix). Reuses the scripted transport (phase-20) and the sandbox
fakes (phase-21) so test-gen is driven entirely by a mock model over the real client + tool
registry — the same approach the codegen tests use.
"""

from __future__ import annotations

from beanie import PydanticObjectId

from app.agents.anthropic_client import ToolUse, TurnComplete
from app.agents.cost import Usage
from app.db.models import RequirementSpec
from app.db.models.enums import CriterionKind
from app.db.models.requirement import AcceptanceCriterion, Feature
from tests.agents.test_client_tool_loop import FakeTransport, ScriptedTurn


def build_testgen_transport(
    plan_text: str,
    tool_uses: list[ToolUse],
    *,
    final_text: str = "Tests written.",
    infer_json: str | None = None,
) -> FakeTransport:
    """Script the run_tool_loop turns the agent consumes in order.

    Order: [infer (Haiku, JSON) if infer_json] → plan (Haiku) → author (Sonnet: tool turn(s) +
    final summary). Each ``run_tool_loop`` call pops from the same transport.
    """
    turns: list[ScriptedTurn] = []
    if infer_json is not None:
        turns.append(
            ScriptedTurn(
                deltas=[infer_json], turn=TurnComplete(text=infer_json, usage=Usage(30, 20))
            )
        )
    turns.append(
        ScriptedTurn(deltas=[plan_text], turn=TurnComplete(text=plan_text, usage=Usage(50, 20)))
    )
    if tool_uses:
        turns.append(
            ScriptedTurn(
                deltas=["Writing tests…"],
                turn=TurnComplete(
                    text="Writing tests…",
                    tool_uses=tool_uses,
                    usage=Usage(200, 60),
                    stop_reason="tool_use",
                ),
            )
        )
    turns.append(
        ScriptedTurn(deltas=[final_text], turn=TurnComplete(text=final_text, usage=Usage(40, 15)))
    )
    return FakeTransport(turns)


def unit_test_content(criterion_ids: list[str]) -> str:
    """A backend Jest+supertest unit test tagged with its criterion id(s)."""
    tag = ", ".join(criterion_ids)
    cases = "\n".join(
        f"  it('[{cid}] behaves', () => {{ expect(true).toBe(true) }})" for cid in criterion_ids
    )
    return f"// @criteria: {tag}\ndescribe('feature', () => {{\n{cases}\n}})\n"


def e2e_test_content(criterion_ids: list[str]) -> str:
    """A Playwright e2e spec tagged with its criterion id(s)."""
    tag = ", ".join(criterion_ids)
    cases = "\n".join(
        f"test('[{cid}] visible', async ({{ page }}) => {{ await page.goto('/') }})"
        for cid in criterion_ids
    )
    return f"// @criteria: {tag}\nimport {{ test }} from '@playwright/test'\n{cases}\n"


async def seed_spec(project_id: PydanticObjectId, features: list[Feature]) -> RequirementSpec:
    """Insert a requirements spec with caller-chosen criterion ids (deterministic traceability)."""
    return await RequirementSpec(project_id=project_id, features=features).insert()


def feature(name: str, criteria: list[tuple[str, str, CriterionKind]]) -> Feature:
    """Build a Feature from ``(id, text, kind)`` triples."""
    return Feature(
        name=name,
        acceptance_criteria=[
            AcceptanceCriterion(id=cid, text=text, kind=kind) for cid, text, kind in criteria
        ],
    )
