"""Optional AI-assist for requirements capture: draft a **whole spec** from a freeform description.

The sibling of :mod:`app.orchestrator.requirements_suggest`, one level up. Where *suggest* proposes
acceptance criteria for a feature the user has already named, *draft* takes "here's what my app
needs to do" in plain prose and proposes the entire feature list — a faster on-ramp into the very
same guided form (D6), not a replacement for it.

Same three properties as its sibling, deliberately:

- **Cheap** — routed to ``MODEL_ROUTING`` (Haiku) via :data:`TaskKind.summarize`; this is drafting,
  not codegen (golden rule 7). Cost is recorded on a ``Run`` like every other agent call.
- **Never persisted** — the reply is returned as *proposals*. Only the user's explicit save through
  ``RequirementsService.save()`` mints a real ``RequirementSpec`` version, so the human owns the
  spec and criterion-id stability is untouched.
- **Fails soft** — an unparseable reply yields an empty draft rather than an error, and the UI falls
  back to manual entry. Provider failures (quota/auth) surface as ``ProviderError`` with their
  fallback hint, exactly as elsewhere.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.agents.anthropic_client import AnthropicClient
from app.agents.models import TaskKind
from app.db.models import Project, Run
from app.db.models.common import utcnow
from app.db.models.enums import CriterionKind
from app.orchestrator.requirements_suggest import SuggestedCriterion

#: Caps on what one draft may propose. A draft is a starting point the user edits — a 30-feature
#: wall of text is worse than a focused handful, and the bounds also keep the reply cheap to parse.
MAX_FEATURES = 8
MAX_CRITERIA_PER_FEATURE = 6
MAX_LIST_ITEMS = 6
#: Freeform input is user-controlled; cap it so one paste cannot blow the routing model's budget.
MAX_DESCRIPTION_CHARS = 8000

_KINDS = {"unit", "e2e", "either"}

_SYSTEM = (
    "You help a semi-technical user turn a plain-language description of a web app into a "
    "structured requirements spec. Propose the distinct features the app needs, and for each one a "
    "few concise, independently testable acceptance criteria. Prefer a focused set of real "
    "features over an exhaustive list, and never invent a feature the description does not imply. "
    "Reply with ONLY a JSON array of objects "
    '{"name": string, "description": string, "inputs": string[], "expected_behaviors": string[], '
    '"acceptance_criteria": [{"text": string, "kind": "unit"|"e2e"|"either"}]} and nothing else. '
    '"inputs" is data the user provides; "expected_behaviors" is what the user should observe. '
    'Use "unit" for logic a unit test covers, "e2e" for user-visible flows, "either" if unsure.'
)


@dataclass(frozen=True)
class DraftedFeature:
    """One proposed feature — the ``Feature`` shape, minus anything only persistence assigns."""

    name: str
    description: str
    inputs: list[str]
    expected_behaviors: list[str]
    acceptance_criteria: list[SuggestedCriterion]


def _user_prompt(description: str) -> str:
    return (
        f"The app should do the following:\n\n{description.strip()}\n\n"
        "Propose the requirements spec."
    )


def _strings(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        text = str(item).strip()
        if text:
            out.append(text)
        if len(out) >= MAX_LIST_ITEMS:
            break
    return out


def _criteria(raw: Any) -> list[SuggestedCriterion]:
    if not isinstance(raw, list):
        return []
    out: list[SuggestedCriterion] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        kind_raw = str(item.get("kind", "either")).strip().lower()
        kind = kind_raw if kind_raw in _KINDS else "either"
        out.append(SuggestedCriterion(text=text, kind=CriterionKind(kind)))
        if len(out) >= MAX_CRITERIA_PER_FEATURE:
            break
    return out


def _parse(text: str) -> list[DraftedFeature]:
    """Best-effort: pull the JSON array out of the reply, keeping only usable features.

    A feature with no name, or none the save endpoint would accept (it requires ≥1 acceptance
    criterion), is dropped rather than handed to the form as a row the user cannot save.
    """
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        raw = json.loads(text[start : end + 1])
    except (ValueError, TypeError):
        return []
    if not isinstance(raw, list):
        return []

    out: list[DraftedFeature] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name or name.lower() in seen:
            continue  # the spec validator rejects blank + duplicate names, so never propose them
        criteria = _criteria(item.get("acceptance_criteria"))
        if not criteria:
            continue
        seen.add(name.lower())
        out.append(
            DraftedFeature(
                name=name,
                description=str(item.get("description", "")).strip(),
                inputs=_strings(item.get("inputs")),
                expected_behaviors=_strings(item.get("expected_behaviors")),
                acceptance_criteria=criteria,
            )
        )
        if len(out) >= MAX_FEATURES:
            break
    return out


async def draft_features(
    project: Project, description: str, *, client: AnthropicClient | None = None
) -> list[DraftedFeature]:
    """Ask Haiku for an editable, unsaved draft spec (cost-tracked on a ``Run``)."""
    description = description.strip()
    if not description:
        return []
    prompt = _user_prompt(description[:MAX_DESCRIPTION_CHARS])
    agent = client or AnthropicClient()
    run = await Run(project_id=project.id, kind="requirements:draft").insert()
    try:
        result = await agent.run_tool_loop(
            task_kind=TaskKind.summarize,  # → MODEL_ROUTING (Haiku)
            project_id=project.id,  # type: ignore[arg-type]
            run=run,
            system=_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
    finally:
        run.finished_at = utcnow()
        await run.save()
    return _parse(result.text)
