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

from app.core.errors import ProviderError, UserError
from app.db.blobs import get_blob_store
from app.db.models.enums import ArtifactType, MessageRole, Stage, StageStatus
from app.design.base import DesignImage, DesignProvider, DesignResult
from app.design.questions import DesignQuestionService, answered_prompt, clarification_detail
from app.design.registry import resolve_active_key
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
    from app.db.models import DesignQuestion, Project, RequirementSpec
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
        """Route one design turn — and treat a provider's question as a turn, not a failure.

        A provider that answers with a question rather than a design is not broken: it is waiting
        on a decision only the user can make (who the app is for, which half of the scope first).
        Failing over to the next provider "resolved" that by designing something nobody asked for,
        so a ``clarification`` is parked as a :class:`DesignQuestion` and put to the user instead.
        Their next plain message answers it and the original call resumes.
        """
        project_id = _project_id(ctx)
        intake = DesignIntake.model_validate(intent.payload or {})
        questions = DesignQuestionService()
        pending = await questions.pending(project_id)

        #: Set when this turn is *answering* a question — a follow-up question then inherits what
        #: that one was resuming, since the empty intake of an answer no longer says.
        resuming: DesignQuestion | None = None
        try:
            if pending is not None:
                if intake.dismiss_question:
                    return await self._design_without(pending, ctx)
                if _answers_question(intake, intent):
                    resuming = pending
                    return await self._answer_question(pending, intent, ctx)
                # Anything else — a new prompt, screenshots, an import, a refine aimed at a
                # screen — is the user moving on, so the question is no longer what they are
                # being asked.
                await questions.dismiss(project_id)
            return await self._run(intent, ctx, intake)
        except ProviderError as exc:
            detail = clarification_detail(exc)
            if detail is None:
                raise
            # Covers the answered call too: a provider that asks a *follow-up* question parks that
            # one and asks again, rather than surfacing as an error on a turn the user got right.
            return await self._park_question(detail, intent, ctx, intake, resuming)

    async def _run(self, intent: Intent, ctx: StageContext, intake: DesignIntake) -> StageResult:
        """Generate / import / refine, exactly as the intake asks."""
        project = ctx.project
        project_id = _project_id(ctx)

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

        return _designed(summary, text, meta)

    # -- clarifying questions (the provider is waiting on the user) -------------------------

    async def _park_question(
        self,
        detail: dict[str, object],
        intent: Intent,
        ctx: StageContext,
        intake: DesignIntake,
        resuming: DesignQuestion | None = None,
    ) -> StageResult:
        """Record the provider's question and hand it to the user, instead of failing over.

        No artifact is produced — this turn generated nothing — so the stage stays
        ``awaiting_user`` with the question as the assistant's reply. The user's next plain
        message lands in :meth:`_answer_question`.
        """
        project_id = _project_id(ctx)
        message = (intent.message or "").strip()
        if resuming is not None:
            # A follow-up to an answer: it resumes whatever the first question did, and the brief
            # is the answered prompt — not this turn's message, which is only half of it.
            source, design_ref = resuming.source, resuming.design_ref
            fallback_prompt = answered_prompt(resuming, message)
        else:
            source, design_ref = await self._resume_target(intake, intent, project_id)
            fallback_prompt = (intake.text or message or "").strip()
        provider = str(detail.get("provider") or "").strip() or resolve_active_key(ctx.project)
        question = await DesignQuestionService().record(
            project_id,
            provider,
            detail,
            # Only used when the provider did not echo the prompt it received.
            prompt=fallback_prompt,
            source=source,
            design_ref=design_ref,
        )
        summary = (
            f"{provider} needs one decision before it can design this:\n\n"
            f"{question.question}\n\n"
            f"Reply with your answer, or choose \u201cDesign without {provider}\u201d to go ahead "
            f"without it."
        )
        return StageResult(
            messages=[OutboundMessage(role=MessageRole.assistant, content=summary)],
            next_status=StageStatus.awaiting_user,
            events=[
                {
                    "event": "design.question",
                    "payload": {
                        "stage": "design",
                        "provider": provider,
                        "question": question.question,
                        "suggestions": question.suggestions,
                    },
                }
            ],
        )

    async def _answer_question(
        self, pending: DesignQuestion, intent: Intent, ctx: StageContext
    ) -> StageResult:
        """Send the user's answer back to the provider that asked, and resume its call."""
        project = ctx.project
        project_id = _project_id(ctx)
        answer = (intent.message or "").strip()
        # Closed *before* the provider round trip: a call that fails must not leave the question
        # pending, or the next message would be read as a second answer to a question the user has
        # already answered. A failure surfaces as an error they can retry from.
        await DesignQuestionService().answer(pending, answer)
        prompt = answered_prompt(pending, answer)

        if pending.source == "refine" and pending.design_ref:
            # A refine keys off a provider-specific ref, so it goes back to the provider that
            # issued it — the same provenance rule any other refine follows.
            target = provider_for_refine(project, pending.provider)
            result = await target.provider.refine(pending.design_ref, prompt)
            latest = await DesignService().latest(project_id)
            text, meta = design_artifact_fields(
                result,
                source="refine",
                refined_from=latest.version if latest is not None else None,
            )
            summary = _summary(f"Answered {pending.provider} and refined the design.", target.note)
        else:
            outcome = await invoke_with_fallback(
                project,
                DesignCapability.from_text,
                _into_workspace(
                    project,
                    lambda p, ws, title: p.generate_from_text(
                        prompt, workspace=ws, workspace_title=title
                    ),
                    # The asking call already opened a container for this project but produced no
                    # artifact to record it on; reusing it keeps the answer in the same Stitch
                    # project instead of opening a second one beside it.
                    prefer=(pending.provider, pending.workspace),
                ),
            )
            result = outcome.result
            text, meta = design_artifact_fields(result, source="text")
            summary = _summary(
                f"Answered {pending.provider} and generated a design via {result.provider}.",
                outcome.note(),
            )
        return _designed(summary, text, meta)

    async def _design_without(self, pending: DesignQuestion, ctx: StageContext) -> StageResult:
        """The escape hatch: close the question unanswered and design without that provider.

        Keeps the stage's "fail soft, always offer a way forward" promise for a user who would
        rather have *a* design than answer — the asking provider is excluded, so the fallback
        chain (figma/fake) serves the same brief instead.
        """
        project = ctx.project
        project_id = _project_id(ctx)
        await DesignQuestionService().dismiss(project_id)

        context = await _requirements_context(project_id)
        prompt = (
            pending.prompt.strip()
            or _with_requirements(project.original_prompt, context)
            or _default_prompt(project.name)
        )
        outcome = await invoke_with_fallback(
            project,
            DesignCapability.from_text,
            _into_workspace(
                project,
                lambda p, ws, title: p.generate_from_text(
                    prompt, workspace=ws, workspace_title=title
                ),
            ),
            exclude={pending.provider},
        )
        result = outcome.result
        text, meta = design_artifact_fields(result, source="text")
        return _designed(
            f"Left {pending.provider}'s question unanswered and generated a design via "
            f"{result.provider}.",
            text,
            meta,
        )

    async def _resume_target(
        self, intake: DesignIntake, intent: Intent, project_id: PydanticObjectId
    ) -> tuple[str, str]:
        """Which call the question interrupted, so the answer resumes that one.

        Mirrors the branches of :meth:`_run` — the intake decides there and must decide the same
        thing here.
        """
        if intake.image_refs:
            return "image", ""
        if (intake.text or "").strip():
            return "text", ""
        latest = await DesignService().latest(project_id)
        if latest is not None and (intent.message or "").strip():
            ref = (intake.design_ref or "").strip() or str(latest.meta.get(META_EXTERNAL_REF) or "")
            return "refine", ref
        return "text", ""

    async def _load_images(self, refs: list[DesignImageRef]) -> list[DesignImage]:
        blobs = get_blob_store()
        images: list[DesignImage] = []
        for ref in refs:
            data = await blobs.get(ref.ref)
            images.append(DesignImage(filename=ref.filename, media_type=ref.media_type, data=data))
        return images


def _answers_question(intake: DesignIntake, intent: Intent) -> bool:
    """Whether this turn is the answer to the pending question.

    A plain message and nothing else — which is exactly what the chat composer and the question
    card's own box send. Anything carrying intake (a new prompt, screenshots, an import, or a
    refine aimed at a specific screen) is a different request and is handled as one.
    """
    if intake.image_refs or intake.own_design is not None:
        return False
    if (intake.text or "").strip() or (intake.design_ref or "").strip():
        return False
    return bool((intent.message or "").strip())


def _designed(summary: str, text: str, meta: dict[str, object]) -> StageResult:
    """The common result of a turn that produced a design version."""
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


def _into_workspace(
    project: Project,
    call: Callable[[DesignProvider, str | None, str], Awaitable[DesignResult]],
    *,
    prefer: tuple[str, str] | None = None,
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
        if workspace is None and prefer is not None and provider.key == prefer[0] and prefer[1]:
            # `prefer` names a container this project already has with `provider` that no artifact
            # records yet — the one a clarifying question was asked from. Only ever a fallback for
            # the artifact lookup, which stays the source of truth.
            workspace = prefer[1]
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
