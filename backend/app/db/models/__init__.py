"""Beanie document models for the ``forgeflow_meta`` control-plane database (§6)."""

from __future__ import annotations

from beanie import Document

from app.db.models.artifact import Artifact
from app.db.models.config_audit import ConfigAudit
from app.db.models.credential import Credential
from app.db.models.deployment import STATUS_DELETED, Deployment, DeploymentRef
from app.db.models.design_question import DesignQuestion, DesignQuestionStatus
from app.db.models.design_quota import DesignQuota
from app.db.models.message import Message
from app.db.models.platform_setting import PlatformSetting
from app.db.models.project import Project
from app.db.models.repair_attempt import RepairAttempt
from app.db.models.requirement import AcceptanceCriterion, Feature, RequirementSpec
from app.db.models.run import Cost, Run, RunProgress
from app.db.models.stage_state import StageState
from app.db.models.test_run import TestRun
from app.db.models.test_suite import TestSuite
from app.db.models.user import User

# Registered with Beanie on init (order-independent).
ALL_DOCUMENT_MODELS: list[type[Document]] = [
    User,
    Credential,
    ConfigAudit,
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
    DesignQuestion,
]

__all__ = [
    "ALL_DOCUMENT_MODELS",
    "STATUS_DELETED",
    "Artifact",
    "ConfigAudit",
    "Cost",
    "Credential",
    "Deployment",
    "DeploymentRef",
    "DesignQuestion",
    "DesignQuestionStatus",
    "DesignQuota",
    "AcceptanceCriterion",
    "Feature",
    "Message",
    "PlatformSetting",
    "Project",
    "RepairAttempt",
    "RequirementSpec",
    "Run",
    "RunProgress",
    "StageState",
    "TestRun",
    "TestSuite",
    "User",
]
