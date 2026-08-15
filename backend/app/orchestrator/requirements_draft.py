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
#: Names that are worse than none — the user would only have to clear them.
_PLACEHOLDER_NAMES = {"my app", "web app", "app", "my web app", "untitled", "the app"}

#: Cap on the proposed product name — a label for a UI header and a provider project, not a tagline.
MAX_APP_NAME_CHARS = 40

_SYSTEM = (
    "You help a semi-technical user turn a plain-language description of a web app into a "
    "structured requirements spec. Propose the distinct features the app needs, and for each one a "
    "few concise, independently testable acceptance criteria. Prefer a focused set of real "
    "features over an exhaustive list, and never invent a feature the description does not imply. "
    "Also propose a short, memorable product name for the app (1-3 words, title case, no quotes, "
    "no generic placeholders like 'My App' or 'Web App'). "
    'Reply with ONLY a JSON object {"app_name": string, "features": [...]} and nothing else, where '
    "each feature is "
    '{"name": string, "description": string, "inputs": string[], "expected_behaviors": string[], '
    '"acceptance_criteria": [{"text": string, "kind": "unit"|"e2e"|"either"}]}. '
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


@dataclass(frozen=True)
class DraftedSpec:
    """A whole proposed spec: the feature list plus a name for the app itself.

    The name is carried separately rather than as a feature because it has nothing testable about
    it — modelling it as a criterion-less ``Feature`` would put a fake row in the spec and, worse,
    in the test suite generated from it.
    """

    app_name: str
    features: list[DraftedFeature]


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


def _app_name(raw: Any) -> str:
    """A usable product name, or blank — never a placeholder the user would just have to delete."""
    name = " ".join(str(raw or "").split())
    if not name or name.lower() in _PLACEHOLDER_NAMES:
        return ""
    return name[:MAX_APP_NAME_CHARS]


def _payload(text: str) -> tuple[str, Any]:
    """``(app name, raw feature list)`` from the reply.

    The documented shape is an object, but a model that answers with the bare feature array (the
    older contract, and a common drift) still yields a perfectly good draft — so that is read too
    rather than discarded for missing a name.

    Which of the two it is, is decided by whichever bracket opens **first**: a feature array is
    full of ``{`` too, so scanning for an object first would slice one element out of the middle of
    an otherwise perfectly good array and read it as the whole reply.
    """
    obj_at, arr_at = text.find("{"), text.find("[")
    array_first = arr_at != -1 and (obj_at == -1 or arr_at < obj_at)

    parsed = _loads(text, "[", "]") if array_first else _loads(text, "{", "}")
    if parsed is None:  # the preferred shape did not parse — try the other one
        parsed = _loads(text, "{", "}") if array_first else _loads(text, "[", "]")

    if isinstance(parsed, dict):
        return _app_name(parsed.get("app_name")), parsed.get("features")
    return "", parsed


def _loads(text: str, opener: str, closer: str) -> Any:
    """The outermost ``opener…closer`` slice of ``text``, parsed, or ``None``."""
    start, end = text.find(opener), text.rfind(closer)
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except (ValueError, TypeError):
        return None


def _parse(text: str) -> DraftedSpec:
    """Best-effort: pull the draft out of the reply, keeping only usable features.

    A feature with no name, or none the save endpoint would accept (it requires ≥1 acceptance
    criterion), is dropped rather than handed to the form as a row the user cannot save.
    """
    app_name, raw = _payload(text)
    if not isinstance(raw, list):
        return DraftedSpec(app_name=app_name, features=[])

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
    return DraftedSpec(app_name=app_name, features=out)


async def draft_spec(
    project: Project, description: str, *, client: AnthropicClient | None = None
) -> DraftedSpec:
    """Ask Haiku for an editable, unsaved draft spec (cost-tracked on a ``Run``)."""
    description = description.strip()
    if not description:
        return DraftedSpec(app_name="", features=[])
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
