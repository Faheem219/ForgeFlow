"""Concrete repositories (one class per aggregate). Dict-style queries keep these
type-checker-friendly; str-backed enums encode directly as their values.
"""

from __future__ import annotations

from typing import Any

from beanie import PydanticObjectId
from pymongo.errors import DuplicateKeyError

from app.db.models import (
    Artifact,
    Credential,
    Deployment,
    Feature,
    Message,
    PlatformSetting,
    Project,
    RepairAttempt,
    RequirementSpec,
    Run,
    StageState,
    TestRun,
    TestSuite,
    User,
)
from app.db.models.common import utcnow
from app.db.models.enums import (
    ArtifactType,
    SettingCategory,
    Stage,
    StageStatus,
    TestEnv,
    TestKind,
)
from app.db.repos.base import BaseRepo


class UserRepo(BaseRepo[User]):
    def __init__(self) -> None:
        super().__init__(User)

    async def get_by_email(self, email: str) -> User | None:
        return await User.find_one({"email": email})


class CredentialRepo(BaseRepo[Credential]):
    def __init__(self) -> None:
        super().__init__(Credential)

    async def list_for_user(self, user_id: PydanticObjectId) -> list[Credential]:
        return await Credential.find({"user_id": user_id}).to_list()


class ProjectRepo(BaseRepo[Project]):
    def __init__(self) -> None:
        super().__init__(Project)

    async def list_for_user(self, user_id: PydanticObjectId) -> list[Project]:
        return await Project.find({"user_id": user_id}).sort("-created_at").to_list()


class StageStateRepo(BaseRepo[StageState]):
    def __init__(self) -> None:
        super().__init__(StageState)

    async def get_or_create(self, project_id: PydanticObjectId, stage: Stage) -> StageState:
        found = await StageState.find_one({"project_id": project_id, "stage": stage})
        if found is not None:
            return found
        state = StageState(project_id=project_id, stage=stage)
        try:
            return await state.insert()
        except DuplicateKeyError:
            # Lost a create race; the concurrent writer's row is authoritative.
            found = await StageState.find_one({"project_id": project_id, "stage": stage})
            if found is None:
                raise
            return found

    async def list_for_project(self, project_id: PydanticObjectId) -> list[StageState]:
        return await StageState.find({"project_id": project_id}).to_list()

    async def set_status(
        self, project_id: PydanticObjectId, stage: Stage, status: StageStatus
    ) -> StageState:
        state = await self.get_or_create(project_id, stage)
        state.status = status
        state.updated_at = utcnow()
        await state.save()
        return state


class MessageRepo(BaseRepo[Message]):
    def __init__(self) -> None:
        super().__init__(Message)

    async def list_for_project(
        self,
        project_id: PydanticObjectId,
        stage: Stage | None = None,
        *,
        skip: int = 0,
        limit: int | None = None,
    ) -> list[Message]:
        query: dict[str, Any] = {"project_id": project_id}
        if stage is not None:
            query["stage"] = stage
        cursor = Message.find(query).sort("+created_at")
        if skip:
            cursor = cursor.skip(skip)
        if limit is not None:
            cursor = cursor.limit(limit)
        return await cursor.to_list()


class ArtifactRepo(BaseRepo[Artifact]):
    def __init__(self) -> None:
        super().__init__(Artifact)

    async def latest_version(
        self, project_id: PydanticObjectId, stage: Stage, artifact_type: ArtifactType
    ) -> Artifact | None:
        return await (
            Artifact.find({"project_id": project_id, "stage": stage, "type": artifact_type})
            .sort("-version")
            .first_or_none()
        )

    async def create_version(
        self,
        project_id: PydanticObjectId,
        stage: Stage,
        artifact_type: ArtifactType,
        ref: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> Artifact:
        latest = await self.latest_version(project_id, stage, artifact_type)
        version = (latest.version + 1) if latest is not None else 1
        artifact = Artifact(
            project_id=project_id,
            stage=stage,
            type=artifact_type,
            version=version,
            ref=ref,
            meta=meta or {},
        )
        return await artifact.insert()

    async def get_version(
        self,
        project_id: PydanticObjectId,
        stage: Stage,
        artifact_type: ArtifactType,
        version: int,
    ) -> Artifact | None:
        return await Artifact.find_one(
            {"project_id": project_id, "stage": stage, "type": artifact_type, "version": version}
        )

    async def list_versions(
        self, project_id: PydanticObjectId, stage: Stage, artifact_type: ArtifactType
    ) -> list[Artifact]:
        return await (
            Artifact.find({"project_id": project_id, "stage": stage, "type": artifact_type})
            .sort("+version")
            .to_list()
        )


class RequirementSpecRepo(BaseRepo[RequirementSpec]):
    def __init__(self) -> None:
        super().__init__(RequirementSpec)

    async def latest(self, project_id: PydanticObjectId) -> RequirementSpec | None:
        return await (
            RequirementSpec.find({"project_id": project_id}).sort("-version").first_or_none()
        )

    async def create_version(
        self,
        project_id: PydanticObjectId,
        features: list[Feature],
        *,
        inferred: bool = False,
    ) -> RequirementSpec:
        latest = await self.latest(project_id)
        version = (latest.version + 1) if latest is not None else 1
        spec = RequirementSpec(
            project_id=project_id, features=features, version=version, inferred=inferred
        )
        return await spec.insert()


class TestSuiteRepo(BaseRepo[TestSuite]):
    def __init__(self) -> None:
        super().__init__(TestSuite)

    async def list_for_project(self, project_id: PydanticObjectId) -> list[TestSuite]:
        return await TestSuite.find({"project_id": project_id}).to_list()

    async def latest(self, project_id: PydanticObjectId, kind: TestKind) -> TestSuite | None:
        return await (
            TestSuite.find({"project_id": project_id, "kind": kind})
            .sort("-version")
            .first_or_none()
        )

    async def create_version(
        self,
        project_id: PydanticObjectId,
        kind: TestKind,
        files: list[str],
        generated_from: list[str],
    ) -> TestSuite:
        """Append the next version of a project's ``kind`` suite (unit/e2e), versioned per kind."""
        latest = await self.latest(project_id, kind)
        version = (latest.version + 1) if latest is not None else 1
        suite = TestSuite(
            project_id=project_id,
            kind=kind,
            files=files,
            generated_from=generated_from,
            version=version,
        )
        return await suite.insert()


class TestRunRepo(BaseRepo[TestRun]):
    def __init__(self) -> None:
        super().__init__(TestRun)

    async def list_for_project(self, project_id: PydanticObjectId) -> list[TestRun]:
        return await TestRun.find({"project_id": project_id}).sort("-created_at").to_list()

    async def latest(
        self, project_id: PydanticObjectId, env: TestEnv | None = None
    ) -> TestRun | None:
        """The newest run, optionally restricted to one environment.

        Sandbox and live runs (phase-39) share this collection, so callers that mean one of them —
        the repair loop repairs *workspace* code — must say which.
        """
        query: dict[str, Any] = {"project_id": project_id}
        if env is not None:
            query["env"] = env
        return await TestRun.find(query).sort("-created_at").first_or_none()


class RepairAttemptRepo(BaseRepo[RepairAttempt]):
    def __init__(self) -> None:
        super().__init__(RepairAttempt)

    async def list_for_project(self, project_id: PydanticObjectId) -> list[RepairAttempt]:
        return await RepairAttempt.find({"project_id": project_id}).sort("+created_at").to_list()


class DeploymentRepo(BaseRepo[Deployment]):
    def __init__(self) -> None:
        super().__init__(Deployment)

    async def list_for_project(self, project_id: PydanticObjectId) -> list[Deployment]:
        return await Deployment.find({"project_id": project_id}).sort("-created_at").to_list()

    async def latest(self, project_id: PydanticObjectId) -> Deployment | None:
        return await Deployment.find({"project_id": project_id}).sort("-created_at").first_or_none()


class RunRepo(BaseRepo[Run]):
    def __init__(self) -> None:
        super().__init__(Run)

    async def list_for_project(self, project_id: PydanticObjectId) -> list[Run]:
        return await Run.find({"project_id": project_id}).sort("-started_at").to_list()


class PlatformSettingRepo(BaseRepo[PlatformSetting]):
    def __init__(self) -> None:
        super().__init__(PlatformSetting)

    async def get_by_key(self, key: str) -> PlatformSetting | None:
        return await PlatformSetting.find_one({"key": key})

    async def list_by_category(self, category: SettingCategory) -> list[PlatformSetting]:
        return await PlatformSetting.find({"category": category}).to_list()

    async def upsert(
        self,
        key: str,
        value: Any,
        category: SettingCategory,
        updated_by: PydanticObjectId | None = None,
    ) -> PlatformSetting:
        existing = await self.get_by_key(key)
        if existing is not None:
            existing.value = value
            existing.category = category
            existing.updated_by = updated_by
            existing.updated_at = utcnow()
            await existing.save()
            return existing
        setting = PlatformSetting(key=key, value=value, category=category, updated_by=updated_by)
        return await setting.insert()
