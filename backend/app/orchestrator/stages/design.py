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

The handler returns an :class:`ArtifactSpec` and lets the **conductor** persist it (single writer),
so the new version is linked to the assistant reply and returned in the intent response. Provider
failures (quota/auth) propagate as :class:`ProviderError` with a ``fallback_hint`` — a clear message
+ fallback, never a crash.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from beanie import PydanticObjectId

from app.core.errors import UserError
from app.db.blobs import get_blob_store
from app.db.models.enums import ArtifactType, MessageRole, Stage, StageStatus
from app.design.base import DesignImage, DesignResult
from app.design.registry import get_active
from app.design.schemas import DesignImageRef, DesignIntake
from app.design.service import META_EXTERNAL_REF, DesignService, design_artifact_fields
from app.orchestrator.schemas import (
    ArtifactSpec,
    Intent,
    IntentAction,
    OutboundMessage,
    StageResult,
)

if TYPE_CHECKING:  # avoid an import cycle: stages/__init__ imports this module
    from app.orchestrator.stages.base import StageContext

OWN_PROVIDER = "own"  # sentinel provider key for bring-your-own designs (no MCP call)


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
            result = await get_active(project).generate_from_image(images, intake.text)
            text, meta = design_artifact_fields(result, source="image")
            summary = f"Generated a design from {len(images)} screenshot(s) via {result.provider}."
        elif intake.text and intake.text.strip():
            result = await get_active(project).generate_from_text(intake.text.strip())
            text, meta = design_artifact_fields(result, source="text")
            summary = f"Generated a design from your prompt via {result.provider}."
        else:
            # No new intake → refine the latest design via the provider, using the chat instruction.
            instruction = (intent.message or "").strip()
            if not instruction:
                raise UserError(
                    "Provide a prompt, screenshots, an imported design, or a refine instruction."
                )
            latest = await DesignService().latest(project_id)
            if latest is None:
                raise UserError("There's no design to refine yet — generate or import one first.")
            external_ref = latest.meta.get(META_EXTERNAL_REF)
            if not isinstance(external_ref, str) or not external_ref:
                raise UserError(
                    "The current design was imported and has no provider reference; regenerate "
                    "from a prompt or screenshots to change it."
                )
            result = await get_active(project).refine(external_ref, instruction)
            text, meta = design_artifact_fields(
                result, source="refine", refined_from=latest.version
            )
            summary = f"Refined the design: {instruction}"

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


def _project_id(ctx: StageContext) -> PydanticObjectId:
    project_id = ctx.project.id
    if project_id is None:  # pragma: no cover - a persisted project always carries an id
        raise UserError("Project is not persisted")
    return project_id
