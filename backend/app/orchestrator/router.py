"""Top-level artifact routes (phase-07): fetch a single artifact + diff two artifacts.

Ownership is enforced via the artifact's owning project — an artifact the caller doesn't own is
reported as ``404`` (its existence is never leaked).
"""

from __future__ import annotations

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends

from app.auth.deps import get_current_user_id
from app.core.errors import NotFoundError
from app.core.limits import expensive_rate_limit
from app.db.models import Artifact
from app.db.repos import RunRepo
from app.orchestrator.artifacts import ArtifactService
from app.orchestrator.conductor import BUILD_RUN_KINDS, Conductor
from app.orchestrator.requirements import (
    DraftedFeaturePublic,
    DraftRequest,
    DraftResponse,
    RequirementSpecInput,
    RequirementSpecPublic,
    RequirementsService,
    SuggestedCriterionPublic,
    SuggestRequest,
    SuggestResponse,
)
from app.orchestrator.requirements_draft import draft_spec
from app.orchestrator.requirements_suggest import suggest_criteria
from app.orchestrator.schemas import (
    ActivityPublic,
    ArtifactDetail,
    ArtifactDiffResponse,
    BuildActivityPublic,
    Intent,
    IntentRequest,
    IntentResponse,
)
from app.projects.service import ProjectService, parse_object_id

router = APIRouter(prefix="/artifacts", tags=["artifacts"])
intents_router = APIRouter(prefix="/projects", tags=["orchestrator"])
requirements_router = APIRouter(prefix="/projects", tags=["requirements"])


async def _owned_artifact(artifact_id: str, user_id: PydanticObjectId) -> Artifact:
    artifact = await ArtifactService().get_by_id(parse_object_id(artifact_id, "Artifact"))
    if artifact is None:
        raise NotFoundError("Artifact not found")
    try:
        await ProjectService().get_owned(artifact.project_id, user_id)
    except NotFoundError as exc:
        # Don't leak that the artifact exists under someone else's project.
        raise NotFoundError("Artifact not found") from exc
    return artifact


@router.get("/{artifact_id}", response_model=ArtifactDetail)
async def get_artifact(
    artifact_id: str, user_id: PydanticObjectId = Depends(get_current_user_id)
) -> ArtifactDetail:
    artifact = await _owned_artifact(artifact_id, user_id)
    content = await ArtifactService().get_content(artifact)
    return ArtifactDetail.from_artifact_with_content(artifact, content)


@router.get("/{a}/diff/{b}", response_model=ArtifactDiffResponse)
async def diff_artifacts(
    a: str, b: str, user_id: PydanticObjectId = Depends(get_current_user_id)
) -> ArtifactDiffResponse:
    artifact_a = await _owned_artifact(a, user_id)
    artifact_b = await _owned_artifact(b, user_id)
    diff = await ArtifactService().diff(artifact_a, artifact_b)
    return ArtifactDiffResponse(a=str(artifact_a.id), b=str(artifact_b.id), diff=diff)


@intents_router.post(
    "/{project_id}/intent",
    response_model=IntentResponse,
    dependencies=[Depends(expensive_rate_limit)],
)
async def submit_intent(
    project_id: str,
    body: IntentRequest,
    user_id: PydanticObjectId = Depends(get_current_user_id),
) -> IntentResponse:
    """Route a user intent through the conductor; detailed progress streams over the WS."""
    intent = Intent.from_request(parse_object_id(project_id), body)
    return await Conductor().handle_intent(user_id, intent)


@intents_router.get("/{project_id}/activity", response_model=ActivityPublic)
async def project_activity(
    project_id: str,
    user_id: PydanticObjectId = Depends(get_current_user_id),
) -> ActivityPublic:
    """What long-running work is in flight for this project, and how far it has got.

    This is the reattach point: a build is detached from the request that started it, so a reloaded
    page has no other way to learn one is running. Progress is read from the ``Run`` document rather
    than the realtime replay ring, which is bounded and long since evicted on a real build.
    """
    pid = await _owned_project_id(project_id, user_id)
    runs = await RunRepo().active_for_project(pid, BUILD_RUN_KINDS)
    if not runs:
        return ActivityPublic(build=None)

    # Prefer the codegen run — it is the one carrying progress. The conductor's own trace opens
    # first, so it is the fallback for the brief window before codegen starts.
    detailed = next((r for r in runs if r.kind == "codegen:build"), runs[0])
    return ActivityPublic(
        build=BuildActivityPublic(
            run_id=str(detailed.id),
            started_at=detailed.started_at,
            step=detailed.progress.step,
            label=detailed.progress.label,
            target=detailed.progress.target,
            files=list(detailed.progress.files),
            phase_index=detailed.progress.phase_index,
            phase_total=detailed.progress.phase_total,
            phase_id=detailed.progress.phase_id,
        )
    )


# --------------------------------------------------------------------- requirements (phase-25)


async def _owned_project_id(project_id: str, user_id: PydanticObjectId) -> PydanticObjectId:
    pid = parse_object_id(project_id)
    await ProjectService().get_owned(pid, user_id)  # 404 if missing / not owned
    return pid


@requirements_router.get("/{project_id}/requirements", response_model=RequirementSpecPublic)
async def get_requirements(
    project_id: str, user_id: PydanticObjectId = Depends(get_current_user_id)
) -> RequirementSpecPublic:
    pid = await _owned_project_id(project_id, user_id)
    spec = await RequirementsService().latest(pid)
    if spec is None:
        raise NotFoundError("No requirements for this project yet")
    return RequirementSpecPublic.of(spec)


@requirements_router.post("/{project_id}/requirements", response_model=RequirementSpecPublic)
@requirements_router.put("/{project_id}/requirements", response_model=RequirementSpecPublic)
async def save_requirements(
    project_id: str,
    body: RequirementSpecInput,
    user_id: PydanticObjectId = Depends(get_current_user_id),
) -> RequirementSpecPublic:
    """Create/update the spec — a new version only when the content changes (append-only)."""
    pid = await _owned_project_id(project_id, user_id)
    spec = await RequirementsService().save(pid, body)
    return RequirementSpecPublic.of(spec)


@requirements_router.get(
    "/{project_id}/requirements/versions", response_model=list[RequirementSpecPublic]
)
async def list_requirement_versions(
    project_id: str, user_id: PydanticObjectId = Depends(get_current_user_id)
) -> list[RequirementSpecPublic]:
    pid = await _owned_project_id(project_id, user_id)
    specs = await RequirementsService().list_versions(pid)
    return [RequirementSpecPublic.of(s) for s in specs]


@requirements_router.post(
    "/{project_id}/requirements/suggest",
    response_model=SuggestResponse,
    dependencies=[Depends(expensive_rate_limit)],
)
async def suggest_requirements(
    project_id: str,
    body: SuggestRequest,
    user_id: PydanticObjectId = Depends(get_current_user_id),
) -> SuggestResponse:
    """Optional, cheap AI-assist: propose editable acceptance criteria (Haiku)."""
    pid = await _owned_project_id(project_id, user_id)
    project = await ProjectService().get_owned(pid, user_id)
    suggestions = await suggest_criteria(project, body.name, body.description)
    return SuggestResponse(
        criteria=[SuggestedCriterionPublic(text=s.text, kind=s.kind) for s in suggestions]
    )


@requirements_router.post(
    "/{project_id}/requirements/draft",
    response_model=DraftResponse,
    dependencies=[Depends(expensive_rate_limit)],
)
async def draft_requirements(
    project_id: str,
    body: DraftRequest,
    user_id: PydanticObjectId = Depends(get_current_user_id),
) -> DraftResponse:
    """Whole-spec AI-assist: a freeform description → an editable draft feature list (Haiku).

    The draft itself is never persisted — it seeds the guided form, and only an explicit save
    creates a ``RequirementSpec`` version. The *description* is, the first time one is given
    (``Project.original_prompt``, set once and never overwritten): later stages — Design, most
    directly — reuse it so the user is never made to re-explain an idea they already gave once.
    """
    pid = await _owned_project_id(project_id, user_id)
    project = await ProjectService().get_owned(pid, user_id)
    description = body.description.strip()
    if description and not project.original_prompt:
        project.original_prompt = description
        await project.save()
    draft = await draft_spec(project, body.description)
    return DraftResponse(
        app_name=draft.app_name,
        features=[
            DraftedFeaturePublic(
                name=f.name,
                description=f.description,
                inputs=f.inputs,
                expected_behaviors=f.expected_behaviors,
                acceptance_criteria=[
                    SuggestedCriterionPublic(text=c.text, kind=c.kind)
                    for c in f.acceptance_criteria
                ],
            )
            for f in draft.features
        ],
    )
