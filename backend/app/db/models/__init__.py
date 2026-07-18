"""Beanie document models for the ``forgeflow_meta`` control-plane database (§6)."""

from __future__ import annotations

from beanie import Document

from app.db.models.artifact import Artifact
from app.db.models.credential import Credential
from app.db.models.deployment import Deployment
from app.db.models.design_quota import DesignQuota
from app.db.models.message import Message
from app.db.models.platform_setting import PlatformSetting
from app.db.models.project import Project
from app.db.models.repair_attempt import RepairAttempt
from app.db.models.requirement import AcceptanceCriterion, Feature, RequirementSpec
from app.db.models.run import Cost, Run
from app.db.models.stage_state import StageState
from app.db.models.test_run import TestRun
from app.db.models.test_suite import TestSuite
from app.db.models.user import User

# Registered with Beanie on init (order-independent).
ALL_DOCUMENT_MODELS: list[type[Document]] = [
    User,
    Credential,
    Project,
    StageState,
    Message,
    Artifact,
    RequirementSpec,
    TestSuite,
    TestRun,
    RepairAttempt,
    Deployment,
    Run,
    PlatformSetting,
    DesignQuota,
]

__all__ = [
    "ALL_DOCUMENT_MODELS",
    "Artifact",
    "Cost",
    "Credential",
    "Deployment",
    "DesignQuota",
    "AcceptanceCriterion",
    "Feature",
    "Message",
    "PlatformSetting",
    "Project",
    "RepairAttempt",
    "RequirementSpec",
    "Run",
    "StageState",
    "TestRun",
    "TestSuite",
    "User",
]
