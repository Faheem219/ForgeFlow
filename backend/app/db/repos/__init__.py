"""Repositories — the only DB access surface for services."""

from __future__ import annotations

from app.db.repos.base import BaseRepo
from app.db.repos.repos import (
    ArtifactRepo,
    CredentialRepo,
    DeploymentRepo,
    MessageRepo,
    PlatformSettingRepo,
    ProjectRepo,
    RepairAttemptRepo,
    RequirementSpecRepo,
    RunRepo,
    StageStateRepo,
    TestRunRepo,
    TestSuiteRepo,
    UserRepo,
)

__all__ = [
    "ArtifactRepo",
    "BaseRepo",
    "CredentialRepo",
    "DeploymentRepo",
    "MessageRepo",
    "PlatformSettingRepo",
    "ProjectRepo",
    "RepairAttemptRepo",
    "RequirementSpecRepo",
    "RunRepo",
    "StageStateRepo",
    "TestRunRepo",
    "TestSuiteRepo",
    "UserRepo",
]
