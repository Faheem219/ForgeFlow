"""Design results → versioned ``design`` artifacts (phase-16).

The provider-agnostic pipeline the stage handler (phase-19) will drive: pick the active provider,
call it, and persist the result as the next `design` artifact version. Nothing here knows about
Stitch or Figma.

**Payload shape:** phase-07 artifacts carry a single text payload, so HTML+CSS ride together in one
JSON envelope. That keeps a design version atomic (markup and styles can never drift apart across
versions) and inherits phase-07's inline-vs-blob strategy for free — large designs land in the blob
store automatically. Structured fields (provider, external ref, preview image) live in ``meta``, so
a later ``fetch_code``/``refine`` can find the provider's handle without parsing the payload.
"""

from __future__ import annotations

from beanie import PydanticObjectId
from pydantic import BaseModel, Field

from app.core.errors import UserError
from app.db.models import Artifact, Project
from app.db.models.enums import ArtifactType, Stage
from app.design.base import DesignImage, DesignResult, DesignScreen
from app.design.registry import get_active
from app.design.resilient import DesignCapability, invoke_with_fallback
from app.orchestrator.artifacts import ArtifactService

# meta keys (kept as constants — phase-19/23 read these back)
META_PROVIDER = "provider"
META_EXTERNAL_REF = "external_ref"
META_PREVIEW_IMAGE = "preview_image"
META_SOURCE = "source"
META_REFINED_FROM = "refined_from_version"
META_PROVIDER_META = "provider_meta"


class DesignPayload(BaseModel):
    html: str
    css: str
    #: Every screen the provider returned, when it returned more than one. ``html``/``css`` stay the
    #: primary screen, so a consumer that doesn't know about this field is unaffected.
    screens: list[DesignScreen] = Field(default_factory=list)


def design_artifact_fields(
    result: DesignResult, *, source: str, refined_from: int | None = None
) -> tuple[str, dict[str, object]]:
    """Map a :class:`DesignResult` → the ``(text, meta)`` of a versioned ``design`` artifact.

    Pure (no I/O) so both :meth:`DesignService.save_result` and the design stage handler
    (phase-19, which lets the conductor persist) build identical artifacts.
    """
    payload = DesignPayload(html=result.html, css=result.css, screens=result.screens)
    meta: dict[str, object] = {
        META_PROVIDER: result.provider,
        META_EXTERNAL_REF: result.external_ref,
        META_PREVIEW_IMAGE: result.preview_image,
        META_SOURCE: source,
        # Nested so provider-specific keys can never clobber ours.
        META_PROVIDER_META: result.meta,
    }
    if refined_from is not None:
        meta[META_REFINED_FROM] = refined_from
    return payload.model_dump_json(), meta


class DesignService:
    def __init__(self, artifacts: ArtifactService | None = None) -> None:
        self._artifacts = artifacts or ArtifactService()

    # -- persistence ---------------------------------------------------------------------

    async def save_result(
        self,
        project_id: PydanticObjectId,
        result: DesignResult,
        *,
        source: str,
        refined_from: int | None = None,
    ) -> Artifact:
        """Append the next ``design`` artifact version for a provider result."""
        text, meta = design_artifact_fields(result, source=source, refined_from=refined_from)
        return await self._artifacts.create_version(
            project_id,
            Stage.design,
            ArtifactType.design,
            text=text,
            meta=meta,
        )

    async def load_payload(self, artifact: Artifact) -> DesignPayload:
        """Resolve an artifact's HTML/CSS (inline or from the blob store)."""
        content = await self._artifacts.get_content(artifact)
        if not content:
            raise UserError("Design artifact has no payload")
        return DesignPayload.model_validate_json(content)

    async def latest(self, project_id: PydanticObjectId) -> Artifact | None:
        return await self._artifacts.get_latest(project_id, Stage.design, ArtifactType.design)

    async def list_versions(self, project_id: PydanticObjectId) -> list[Artifact]:
        return await self._artifacts.list_versions(project_id, Stage.design, ArtifactType.design)

    # -- provider-driven flows ------------------------------------------------------------

    async def generate_from_text(self, project: Project, prompt: str) -> Artifact:
        prompt = prompt.strip()
        if not prompt:
            raise UserError("A prompt is required")
        outcome = await invoke_with_fallback(
            project, DesignCapability.from_text, lambda p: p.generate_from_text(prompt)
        )
        return await self.save_result(_project_id(project), outcome.result, source="text")

    async def generate_from_images(
        self, project: Project, images: list[DesignImage], prompt: str | None = None
    ) -> Artifact:
        if not images:
            raise UserError("At least one image is required")
        outcome = await invoke_with_fallback(
            project, DesignCapability.from_image, lambda p: p.generate_from_image(images, prompt)
        )
        return await self.save_result(_project_id(project), outcome.result, source="image")

    async def refine(self, project: Project, instruction: str) -> Artifact:
        """Refine the latest design into a **new version** that references the prior one."""
        instruction = instruction.strip()
        if not instruction:
            raise UserError("A refinement instruction is required")

        project_id = _project_id(project)
        previous = await self.latest(project_id)
        if previous is None:
            raise UserError("There is no design to refine yet")

        external_ref = previous.meta.get(META_EXTERNAL_REF)
        if not isinstance(external_ref, str):
            raise UserError("The latest design has no provider reference to refine")

        result = await get_active(project).refine(external_ref, instruction)
        return await self.save_result(
            project_id, result, source="refine", refined_from=previous.version
        )


def _project_id(project: Project) -> PydanticObjectId:
    if project.id is None:  # pragma: no cover - a persisted project always carries an id
        raise UserError("Project is not persisted")
    return project.id
