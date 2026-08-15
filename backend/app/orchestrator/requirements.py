"""Structured requirements: schema + service (phase-25, D6/D13, §6 ``RequirementSpec``).

The fill-in-the-blanks spec that compiles deterministically into tests. Strongly validated
(non-empty/unique feature names, ≥1 acceptance criterion per feature) and versioned like every
artifact (§7). The **load-bearing** invariant is ``AcceptanceCriterion.id`` stability: it is the
join key across requirements → tests (phase-27) → results (phase-28) → the repair loop's minimal
context (phase-29), so ids are preserved across edits wherever a criterion is unchanged.
"""

from __future__ import annotations

import uuid

from beanie import PydanticObjectId
from pydantic import BaseModel, Field

from app.core.errors import UserError
from app.db.models import AcceptanceCriterion, Feature, RequirementSpec
from app.db.models.enums import CriterionKind
from app.db.repos import RequirementSpecRepo

# --------------------------------------------------------------------- input schemas (API body)


class CriterionInput(BaseModel):
    # Round-tripped from a prior version to preserve its id; omit/blank for a new criterion.
    id: str | None = None
    text: str
    kind: CriterionKind = CriterionKind.either


class FeatureInput(BaseModel):
    name: str
    description: str = ""
    inputs: list[str] = Field(default_factory=list)
    expected_behaviors: list[str] = Field(default_factory=list)
    acceptance_criteria: list[CriterionInput] = Field(default_factory=list)


class RequirementSpecInput(BaseModel):
    #: Editable product name for the app. Optional: a spec saved without one is unchanged from
    #: before this existed, and consumers fall back to the ForgeFlow project name.
    app_name: str = ""
    features: list[FeatureInput] = Field(default_factory=list)


class SuggestRequest(BaseModel):
    """Body for the optional AI-assist: propose criteria for one feature (phase-26)."""

    name: str
    description: str = ""


class SuggestedCriterionPublic(BaseModel):
    text: str
    kind: CriterionKind


class SuggestResponse(BaseModel):
    criteria: list[SuggestedCriterionPublic]


class DraftRequest(BaseModel):
    """Body for the whole-spec AI-assist: a freeform description of what the app must do."""

    description: str


class DraftedFeaturePublic(BaseModel):
    """One proposed feature. Deliberately criterion-*id*-less: nothing here is saved, so no
    criterion has a stable join key yet — ids are minted only by ``RequirementsService.save()``."""

    name: str
    description: str = ""
    inputs: list[str] = Field(default_factory=list)
    expected_behaviors: list[str] = Field(default_factory=list)
    acceptance_criteria: list[SuggestedCriterionPublic] = Field(default_factory=list)


class DraftResponse(BaseModel):
    """An editable draft. Empty ``features`` means "no usable draft" — the UI falls back to the
    manual form rather than treating it as a failure."""

    #: A proposed product name for the app. Blank when the model offered nothing usable.
    app_name: str = ""
    features: list[DraftedFeaturePublic] = Field(default_factory=list)


# --------------------------------------------------------------------- output schemas


class CriterionPublic(BaseModel):
    id: str
    text: str
    kind: CriterionKind

    @classmethod
    def of(cls, c: AcceptanceCriterion) -> CriterionPublic:
        return cls(id=c.id, text=c.text, kind=c.kind)


class FeaturePublic(BaseModel):
    name: str
    description: str
    inputs: list[str]
    expected_behaviors: list[str]
    acceptance_criteria: list[CriterionPublic]

    @classmethod
    def of(cls, f: Feature) -> FeaturePublic:
        return cls(
            name=f.name,
            description=f.description,
            inputs=f.inputs,
            expected_behaviors=f.expected_behaviors,
            acceptance_criteria=[CriterionPublic.of(c) for c in f.acceptance_criteria],
        )


class RequirementSpecPublic(BaseModel):
    project_id: str
    version: int
    app_name: str
    features: list[FeaturePublic]
    # True when the test-gen agent inferred this spec (requirements skipped) — labeled for the UI.
    inferred: bool
    created_at: str

    @classmethod
    def of(cls, spec: RequirementSpec) -> RequirementSpecPublic:
        return cls(
            project_id=str(spec.project_id),
            version=spec.version,
            app_name=spec.app_name,
            features=[FeaturePublic.of(f) for f in spec.features],
            inferred=spec.inferred,
            created_at=spec.created_at.isoformat(),
        )


def _mint_criterion_id() -> str:
    return f"ac-{uuid.uuid4().hex[:12]}"


class RequirementsService:
    def __init__(self) -> None:
        self._repo = RequirementSpecRepo()

    async def latest(self, project_id: PydanticObjectId) -> RequirementSpec | None:
        return await self._repo.latest(project_id)

    async def get_version(
        self, project_id: PydanticObjectId, version: int
    ) -> RequirementSpec | None:
        return await RequirementSpec.find_one({"project_id": project_id, "version": version})

    async def list_versions(self, project_id: PydanticObjectId) -> list[RequirementSpec]:
        return await RequirementSpec.find({"project_id": project_id}).sort("+version").to_list()

    async def save(
        self,
        project_id: PydanticObjectId,
        spec: RequirementSpecInput,
        *,
        inferred: bool = False,
    ) -> RequirementSpec:
        """Validate + persist. A new **version** is created only when the content changes; ids are
        preserved for criteria that are unchanged (client-provided id, else content match).

        ``inferred`` marks a spec the test-gen agent derived from design/build artifacts because the
        requirements stage was skipped (phase-27); it only applies when creating a version.
        """
        previous = await self._repo.latest(project_id)
        features = _resolve(spec, previous)  # validates + assigns stable ids
        # An omitted name keeps the one already on file, so saving an edit to the features from a
        # client that predates this field cannot silently wipe it.
        app_name = spec.app_name.strip() or (previous.app_name if previous is not None else "")

        if previous is not None and previous.features == features and previous.app_name == app_name:
            return previous  # no change → no new version
        return await self._repo.create_version(
            project_id, features, inferred=inferred, app_name=app_name
        )


def validate_features(spec: RequirementSpecInput) -> list[Feature]:
    """Validate a standalone spec (no project, no persistence) and mint its criterion ids.

    The evaluation harness (phase-43) checks its benchmark specs with **this** function, so a seed
    the loader accepts is by construction one the requirements API would accept — the schema stays a
    single source of truth rather than two that drift.
    """
    return _resolve(spec, None)


def _resolve(spec: RequirementSpecInput, previous: RequirementSpec | None) -> list[Feature]:
    """Validate the input and turn it into persisted ``Feature``s with stable criterion ids."""
    if not spec.features:
        raise UserError("A requirements spec needs at least one feature")

    prev_by_feature: dict[str, list[AcceptanceCriterion]] = {}
    if previous is not None:
        for f in previous.features:
            prev_by_feature[f.name.strip().lower()] = list(f.acceptance_criteria)

    seen_names: set[str] = set()
    seen_ids: set[str] = set()
    features: list[Feature] = []

    for index, fin in enumerate(spec.features):
        name = fin.name.strip()
        if not name:
            raise UserError("Feature names must not be empty", detail={"feature_index": index})
        key = name.lower()
        if key in seen_names:
            raise UserError(f"Duplicate feature name: {name!r}", detail={"feature_index": index})
        seen_names.add(key)

        if not fin.acceptance_criteria:
            raise UserError(
                f"Feature {name!r} needs at least one acceptance criterion",
                detail={"feature_index": index},
            )

        prev_pool = list(prev_by_feature.get(key, []))
        criteria: list[AcceptanceCriterion] = []
        for c_index, cin in enumerate(fin.acceptance_criteria):
            text = cin.text.strip()
            if not text:
                raise UserError(
                    f"Acceptance criteria for {name!r} must not be empty",
                    detail={"feature_index": index, "criterion_index": c_index},
                )
            cid = _criterion_id(cin, text, prev_pool, seen_ids)
            if cid in seen_ids:
                raise UserError(f"Duplicate criterion id: {cid!r}")
            seen_ids.add(cid)
            criteria.append(AcceptanceCriterion(id=cid, text=text, kind=cin.kind))

        features.append(
            Feature(
                name=name,
                description=fin.description.strip(),
                inputs=[i.strip() for i in fin.inputs if i.strip()],
                expected_behaviors=[b.strip() for b in fin.expected_behaviors if b.strip()],
                acceptance_criteria=criteria,
            )
        )
    return features


def _criterion_id(
    cin: CriterionInput,
    text: str,
    prev_pool: list[AcceptanceCriterion],
    used: set[str],
) -> str:
    """Prefer a client-provided id; else reuse a previous version's id for the same (unchanged)
    text; else mint a fresh one — so unchanged criteria keep their join key across edits."""
    provided = (cin.id or "").strip()
    if provided:
        return provided
    for prev in prev_pool:
        if prev.text == text and prev.id not in used:
            return prev.id
    return _mint_criterion_id()


__all__ = [
    "CriterionInput",
    "DraftRequest",
    "DraftResponse",
    "DraftedFeaturePublic",
    "FeatureInput",
    "RequirementSpecInput",
    "RequirementSpecPublic",
    "RequirementsService",
    "SuggestRequest",
    "SuggestResponse",
    "SuggestedCriterionPublic",
]
