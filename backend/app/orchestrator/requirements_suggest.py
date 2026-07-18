"""Optional AI-assist for requirements capture (phase-26): suggest acceptance criteria from a
feature's name + description.

Intentionally **assistive and cheap** (D6, design note): routed to ``MODEL_ROUTING`` (Haiku), it
returns editable *proposals* — the human owns the spec, and nothing is ever auto-applied.
Best-effort parsing: an unparseable model reply yields no suggestions rather than an error.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from app.agents.anthropic_client import AnthropicClient
from app.agents.models import TaskKind
from app.db.models import Project, Run
from app.db.models.common import utcnow
from app.db.models.enums import CriterionKind

MAX_SUGGESTIONS = 8

_SYSTEM = (
    "You help a semi-technical user capture software requirements. Given a feature, propose a few "
    "concise, independently testable acceptance criteria. Reply with ONLY a JSON array of objects "
    '{"text": string, "kind": "unit"|"e2e"|"either"} and nothing else. Use "unit" for logic a unit '
    'test covers, "e2e" for user-visible flows, "either" if unsure.'
)


@dataclass(frozen=True)
class SuggestedCriterion:
    text: str
    kind: CriterionKind


def _user_prompt(name: str, description: str) -> str:
    desc = description.strip() or "(no description provided)"
    return f"Feature: {name.strip()}\nDescription: {desc}\n\nPropose acceptance criteria."


def _parse(text: str) -> list[SuggestedCriterion]:
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        raw = json.loads(text[start : end + 1])
    except (ValueError, TypeError):
        return []
    if not isinstance(raw, list):
        return []

    out: list[SuggestedCriterion] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        crit_text = str(item.get("text", "")).strip()
        if not crit_text:
            continue
        kind_raw = str(item.get("kind", "either")).strip().lower()
        kind = kind_raw if kind_raw in {"unit", "e2e", "either"} else "either"
        out.append(SuggestedCriterion(text=crit_text, kind=CriterionKind(kind)))
        if len(out) >= MAX_SUGGESTIONS:
            break
    return out


async def suggest_criteria(
    project: Project, name: str, description: str, *, client: AnthropicClient | None = None
) -> list[SuggestedCriterion]:
    """Ask Haiku for editable acceptance-criteria proposals (cost-tracked on a ``Run``)."""
    if not name.strip():
        return []
    agent = client or AnthropicClient()
    run = await Run(project_id=project.id, kind="requirements:suggest").insert()
    try:
        result = await agent.run_tool_loop(
            task_kind=TaskKind.summarize,  # → MODEL_ROUTING (Haiku)
            project_id=project.id,  # type: ignore[arg-type]
            run=run,
            system=_SYSTEM,
            messages=[{"role": "user", "content": _user_prompt(name, description)}],
        )
    finally:
        run.finished_at = utcnow()
        await run.save()
    return _parse(result.text)
