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
from app.design.base import DesignImage, DesignProvider, DesignResult, DesignScreen
from app.design.resilient import DesignCapability, invoke_with_fallback, provider_for_refine
from app.orchestrator.artifacts import ArtifactService

# meta keys (kept as constants — phase-19/23 read these back)
META_PROVIDER = "provider"
META_EXTERNAL_REF = "external_ref"
META_PREVIEW_IMAGE = "preview_image"
META_SOURCE = "source"
META_REFINED_FROM = "refined_from_version"
META_PROVIDER_META = "provider_meta"
#: The provider-side container this project designs into (Stitch: its project id). Read back by
#: :meth:`DesignService.workspace_for` so the next generate lands in the *same* place.
META_WORKSPACE = "workspace"


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
        META_WORKSPACE: result.workspace,
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

    async def workspace_title_for(self, project: Project) -> str:
        """What this project's provider-side container should be called.

        The requirements draft proposes a product name for the app and the user can edit it, so it
        is the best label available — a Stitch project reads "Focus" instead of one of a stack of
        identical "ForgeFlow" entries. Falls back to the ForgeFlow project's own name when
        requirements were skipped or the draft offered nothing usable.
        """
        from app.orchestrator.requirements import RequirementsService

        if project.id is not None:
            spec = await RequirementsService().latest(project.id)
            if spec is not None and spec.app_name.strip():
                return spec.app_name.strip()
        return project.name

    async def workspace_for(self, project_id: PydanticObjectId, provider_key: str) -> str | None:
        """This project's container inside ``provider_key``, or ``None`` if it has none yet.

        Read from the project's own latest design, which is what keeps each ForgeFlow project
        designing into its own provider project. ``None`` is the correct answer for a project that
        has never generated — the provider then makes a fresh container instead of inheriting one,
        and a screen listing returns nothing instead of another project's screens.

        Only a design from the **same** provider counts: a workspace handle is meaningless to a
        different backend, exactly as ``external_ref`` is (see ``provider_for_refine``).
        """
        latest = await self.latest(project_id)
        if latest is None:
            return None
        if latest.meta.get(META_PROVIDER) != provider_key:
            return None

        workspace = latest.meta.get(META_WORKSPACE)
        if isinstance(workspace, str) and workspace:
            return workspace
        # Back-compat: artifacts written before `workspace` existed only carry the compound
        # `{container}/{resource}` external ref.
        external_ref = latest.meta.get(META_EXTERNAL_REF)
        if isinstance(external_ref, str) and "/" in external_ref:
            return external_ref.rsplit("/", 1)[0]
        return None

    # -- provider-driven flows ------------------------------------------------------------

    async def generate_from_text(self, project: Project, prompt: str) -> Artifact:
        prompt = prompt.strip()
        if not prompt:
            raise UserError("A prompt is required")
        project_id = _project_id(project)

        async def call(provider: DesignProvider) -> DesignResult:
            # Resolved per candidate: a workspace handle only means something to the provider that
            # issued it, so a fallback to figma/fake must not inherit Stitch's project id.
            workspace = await self.workspace_for(project_id, provider.key)
            return await provider.generate_from_text(
                prompt, workspace=workspace, workspace_title=await self.workspace_title_for(project)
            )

        outcome = await invoke_with_fallback(project, DesignCapability.from_text, call)
        return await self.save_result(project_id, outcome.result, source="text")

    async def generate_from_images(
        self, project: Project, images: list[DesignImage], prompt: str | None = None
    ) -> Artifact:
        if not images:
            raise UserError("At least one image is required")
        project_id = _project_id(project)

        async def call(provider: DesignProvider) -> DesignResult:
            workspace = await self.workspace_for(project_id, provider.key)
            return await provider.generate_from_image(
                images,
                prompt,
                workspace=workspace,
                workspace_title=await self.workspace_title_for(project),
            )

        outcome = await invoke_with_fallback(project, DesignCapability.from_image, call)
        return await self.save_result(project_id, outcome.result, source="image")

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

        # Follow the design's provenance, not the active provider: an external_ref is only
        # meaningful to the provider that issued it (see provider_for_refine).
        produced_by = previous.meta.get(META_PROVIDER)
        target = provider_for_refine(project, produced_by if isinstance(produced_by, str) else None)
        result = await target.provider.refine(external_ref, instruction)
        return await self.save_result(
            project_id, result, source="refine", refined_from=previous.version
        )


def _project_id(project: Project) -> PydanticObjectId:
    if project.id is None:  # pragma: no cover - a persisted project always carries an id
        raise UserError("Project is not persisted")
    return project.id
