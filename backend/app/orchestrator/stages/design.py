"""Design stage handler (phase-19) — the first real :class:`StageHandler`.

Provider-agnostic (talks only to the active :class:`~app.design.base.DesignProvider`), honoring the
non-linear model (D12). It maps the three UI actions onto the design lifecycle:

- ``refine`` — the workhorse. Depending on the intent payload it **generates** (text / uploaded
  screenshots) or **imports** a bring-your-own design, or (no intake, just an instruction)
  **refines** the latest design via the provider. Always produces a *new* ``design`` version and
  leaves the stage ``awaiting_user`` for review. On a previously-complete stage the conductor's
  refine action also marks downstream stages ``stale``.
- ``proceed`` — **approve** the latest design (→ ``complete``); no provider call.
- ``skip`` — opt out; downstream must tolerate a missing design.

Since requirements moved ahead of design (2026-08-06), a **generate** additionally folds the latest
``RequirementSpec`` into the provider prompt when one exists, so the UI reflects what the app has to
do rather than being drawn from screenshots/text alone. This is a **soft** dependency and stays one:
no prereq, no gate, and with no spec the provider call is byte-for-byte what it was before.

The handler returns an :class:`ArtifactSpec` and lets the **conductor** persist it (single writer),
so the new version is linked to the assistant reply and returned in the intent response. Provider
failures (quota/auth) propagate as :class:`ProviderError` with a ``fallback_hint`` — a clear message
+ fallback, never a crash.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from beanie import PydanticObjectId

from app.core.errors import UserError
from app.db.blobs import get_blob_store
from app.db.models.enums import ArtifactType, MessageRole, Stage, StageStatus
from app.design.base import DesignImage, DesignProvider, DesignResult
from app.design.resilient import DesignCapability, invoke_with_fallback, provider_for_refine
from app.design.schemas import DesignImageRef, DesignIntake
from app.design.service import (
    META_EXTERNAL_REF,
    META_PROVIDER,
    DesignService,
    design_artifact_fields,
)
from app.orchestrator.requirements import RequirementsService
from app.orchestrator.schemas import (
    ArtifactSpec,
    Intent,
    IntentAction,
    OutboundMessage,
    StageResult,
)

if TYPE_CHECKING:  # avoid an import cycle: stages/__init__ imports this module
    from app.db.models import Project, RequirementSpec
    from app.orchestrator.stages.base import StageContext

OWN_PROVIDER = "own"  # sentinel provider key for bring-your-own designs (no MCP call)

#: Caps on the requirements block folded into a design prompt. This is *prompt context*, not a
#: dump: design providers take a prompt, and a spec pasted in full buries the design intent.
REQ_CONTEXT_MAX_FEATURES = 8
REQ_CONTEXT_MAX_CRITERIA = 3


def _default_prompt(project_name: str) -> str:
    """The true last resort for a first-ever generate: no typed prompt, no original_prompt, no
    requirements. Rather than dead-ending on an error, still produce a real starting design — the
    project's own name is the only thing guaranteed to exist, so it at least isn't generic filler.
    Refining or regenerating from a real description afterward is one click away either way.
    """
    return f'A clean, modern website homepage for "{project_name}".'


class DesignStageHandler:
    async def handle(self, intent: Intent, ctx: StageContext) -> StageResult:
        if intent.action is IntentAction.skip:
            return StageResult(
                messages=[
                    OutboundMessage(
                        role=MessageRole.assistant,
                        content="Design skipped — later stages will proceed without a design.",
                    )
                ]
            )
        if intent.action is IntentAction.proceed:
            return await self._approve(ctx)
        return await self._generate_or_refine(intent, ctx)

    # -- proceed ---------------------------------------------------------------------------

    async def _approve(self, ctx: StageContext) -> StageResult:
        project_id = _project_id(ctx)
        latest = await DesignService().latest(project_id)
        if latest is None:
            raise UserError(
                "There's no design to approve yet. Generate or import one first, or skip the stage."
            )
        return StageResult(
            messages=[
                OutboundMessage(
                    role=MessageRole.assistant,
                    content=f"Approved design v{latest.version}.",
                )
            ]
        )

    # -- refine (generate / import / refine) ----------------------------------------------

    async def _generate_or_refine(self, intent: Intent, ctx: StageContext) -> StageResult:
        project = ctx.project
        project_id = _project_id(ctx)
        intake = DesignIntake.model_validate(intent.payload or {})

        if intake.own_design is not None:
            own = intake.own_design
            result = DesignResult(
                provider=OWN_PROVIDER, external_ref="", html=own.html, css=own.css
            )
            text, meta = design_artifact_fields(result, source="own")
            summary = "Imported your design."
        elif intake.image_refs:
            images = await self._load_images(intake.image_refs)
            image_prompt = _with_requirements(intake.text, await _requirements_context(project_id))
            outcome = await invoke_with_fallback(
                project,
                DesignCapability.from_image,
                _into_workspace(
                    project,
                    lambda p, ws, title: p.generate_from_image(
                        images, image_prompt, workspace=ws, workspace_title=title
                    ),
                ),
            )
            result = outcome.result
            text, meta = design_artifact_fields(result, source="image")
            summary = _summary(
                f"Generated a design from {len(images)} screenshot(s) via {result.provider}.",
                outcome.note(),
            )
        elif intake.text and intake.text.strip():
            context = await _requirements_context(project_id)
            prompt = f"{intake.text.strip()}\n\n{context}" if context else intake.text.strip()
            outcome = await invoke_with_fallback(
                project,
                DesignCapability.from_text,
                _into_workspace(
                    project,
                    lambda p, ws, title: p.generate_from_text(
                        prompt, workspace=ws, workspace_title=title
                    ),
                ),
            )
            result = outcome.result
            text, meta = design_artifact_fields(result, source="text")
            summary = _summary(
                f"Generated a design from your prompt via {result.provider}.", outcome.note()
            )
        else:
            latest = await DesignService().latest(project_id)
            if latest is None:
                # Nothing to refine yet, and nothing was typed — rather than making the user repeat
                # an idea they already gave ForgeFlow, fall back to what it already knows: the
                # description that first seeded requirements, plus the requirements themselves. And
                # if there is truly nothing on file, still generate something real rather than
                # dead-ending on an error — a first design is one click away either way, and
                # blocking here is a worse experience than a generic starting point to refine.
                context = await _requirements_context(project_id)
                informed = _with_requirements(project.original_prompt, context)
                fallback = informed or _default_prompt(project.name)
                outcome = await invoke_with_fallback(
                    project,
                    DesignCapability.from_text,
                    _into_workspace(
                        project,
                        lambda p, ws, title: p.generate_from_text(
                            fallback, workspace=ws, workspace_title=title
                        ),
                    ),
                )
                result = outcome.result
                text, meta = design_artifact_fields(result, source="text")
                headline = (
                    "Generated a design from your requirements"
                    if informed
                    else "Generated a starting design — describe your app for something tailored"
                )
                summary = _summary(f"{headline} via {result.provider}.", outcome.note())
            elif not (intent.message or "").strip():
                # A design already exists but nothing was typed and there's no refine instruction
                # either — ambiguous (refine? approve? nothing?), so this still asks, unlike the
                # no-design case above where auto-generating is the obviously right call.
                raise UserError(
                    "Provide a prompt, screenshots, an imported design, or a refine instruction."
                )
            else:
                # No new intake and a design already exists → refine it via the provider, using
                # the chat instruction (guaranteed non-empty by the `elif` above).
                instruction = (intent.message or "").strip()
                # The UI's screen picker names the screen the user is *looking at*; without it a
                # refine would always hit whichever screen was generated last, which is wrong as
                # soon as an app has more than one.
                external_ref = (intake.design_ref or "").strip() or latest.meta.get(
                    META_EXTERNAL_REF
                )
                if not isinstance(external_ref, str) or not external_ref:
                    raise UserError(
                        "The current design was imported and has no provider reference; "
                        "regenerate from a prompt or screenshots to change it."
                    )
                # Refine follows the design's *provenance*, not the active provider: an
                # external_ref only means something to the provider that issued it. After a
                # generate fell back (e.g. Stitch down → `fake`), sending that ref to the
                # still-active Stitch is what produced "Requested entity was not found". See
                # provider_for_refine.
                produced_by = latest.meta.get(META_PROVIDER)
                target = provider_for_refine(
                    project, produced_by if isinstance(produced_by, str) else None
                )
                result = await target.provider.refine(external_ref, instruction)
                text, meta = design_artifact_fields(
                    result, source="refine", refined_from=latest.version
                )
                summary = _summary(f"Refined the design: {instruction}", target.note)

        return StageResult(
            messages=[OutboundMessage(role=MessageRole.assistant, content=summary)],
            artifacts=[
                ArtifactSpec(stage=Stage.design, type=ArtifactType.design, text=text, meta=meta)
            ],
            # Generated/imported — leave it for the user to review, then Approve or Refine.
            next_status=StageStatus.awaiting_user,
            events=[
                {
                    "event": "progress",
                    "payload": {"stage": "design", "status": "awaiting_user", "note": summary},
                }
            ],
        )

    async def _load_images(self, refs: list[DesignImageRef]) -> list[DesignImage]:
        blobs = get_blob_store()
        images: list[DesignImage] = []
        for ref in refs:
            data = await blobs.get(ref.ref)
            images.append(DesignImage(filename=ref.filename, media_type=ref.media_type, data=data))
        return images


def _into_workspace(
    project: Project,
    call: Callable[[DesignProvider, str | None, str], Awaitable[DesignResult]],
) -> Callable[[DesignProvider], Awaitable[DesignResult]]:
    """Wrap a generate so it lands in *this* ForgeFlow project's own provider container.

    The registry hands out one provider instance per process, so without this every project
    generated into whichever container that instance last created — a new project opened showing
    the previous one's screens. The lookup is per candidate provider because a workspace handle is
    only meaningful to the provider that issued it, so a fallback starts its own container rather
    than inheriting one.
    """

    async def run(provider: DesignProvider) -> DesignResult:
        service = DesignService()
        project_id = _project_id_of(project)
        workspace = await service.workspace_for(project_id, provider.key)
        return await call(provider, workspace, await service.workspace_title_for(project))

    return run


def _project_id_of(project: Project) -> PydanticObjectId:
    if project.id is None:  # pragma: no cover - a persisted project always carries an id
        raise UserError("Project is not persisted")
    return project.id


def _summary(base: str, note: str | None) -> str:
    """Fold an optional fallback note into the assistant's summary line."""
    return f"{base} (Note: {note})" if note else base


# -- requirements as design context (soft, never a prerequisite) --------------------------------


async def _requirements_context(project_id: PydanticObjectId) -> str | None:
    """A concise summary of the project's latest requirements, or ``None`` when there are none.

    Read straight from :class:`RequirementsService` — the *content*, not the requirements stage's
    status: a spec that was saved but never marked complete still describes the app, and the two
    have been decoupled since phase-25. ``None`` (skipped stage, empty spec, or a project that
    never visited requirements) leaves the provider call exactly as it was before this existed,
    which is what keeps the hero path — screenshots → deployed URL — working untouched (D12).
    """
    spec = await RequirementsService().latest(project_id)
    if spec is None or not spec.features:
        return None
    return _format_requirements(spec)


def _format_requirements(spec: RequirementSpec) -> str:
    lines = [
        "The app must support the following features — design the UI so they are all achievable:"
    ]
    for feature in spec.features[:REQ_CONTEXT_MAX_FEATURES]:
        line = f"- {feature.name}"
        if feature.description:
            line += f": {feature.description}"
        lines.append(line)
        for criterion in feature.acceptance_criteria[:REQ_CONTEXT_MAX_CRITERIA]:
            lines.append(f"  - {criterion.text}")
    if len(spec.features) > REQ_CONTEXT_MAX_FEATURES:
        lines.append(f"- (+{len(spec.features) - REQ_CONTEXT_MAX_FEATURES} more features)")
    return "\n".join(lines)


def _with_requirements(prompt: str | None, context: str | None) -> str | None:
    """Append the requirements context to an optional prompt, preserving ``None`` when neither."""
    if not context:
        return prompt
    base = (prompt or "").strip()
    return f"{base}\n\n{context}" if base else context


def _project_id(ctx: StageContext) -> PydanticObjectId:
    project_id = ctx.project.id
    if project_id is None:  # pragma: no cover - a persisted project always carries an id
        raise UserError("Project is not persisted")
    return project_id
