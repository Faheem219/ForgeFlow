"""Enumerations for the control-plane domain model (str-backed for BSON storage).

All enums are ``StrEnum`` so their values encode/query directly as strings and Beanie
serializes them to their ``value``.
"""

from __future__ import annotations

from enum import StrEnum


class UserRole(StrEnum):
    user = "user"
    admin = "admin"


class CredentialKind(StrEnum):
    vercel = "vercel"
    render = "render"
    mongo_uri = "mongo_uri"
    stitch = "stitch"
    figma = "figma"


class CredentialScope(StrEnum):
    platform = "platform"
    byo = "byo"


class Stage(StrEnum):
    design = "design"
    requirements = "requirements"
    build = "build"
    test = "test"
    deploy = "deploy"
    validate = "validate"


class StageStatus(StrEnum):
    empty = "empty"
    in_progress = "in_progress"
    awaiting_user = "awaiting_user"
    complete = "complete"
    skipped = "skipped"
    # `stale` is set when an upstream refine invalidates a downstream stage (see phase-06).
    stale = "stale"


class ProjectStatus(StrEnum):
    active = "active"
    archived = "archived"


class MessageRole(StrEnum):
    user = "user"
    assistant = "assistant"
    system = "system"
    tool = "tool"


class ArtifactType(StrEnum):
    design = "design"
    code_change = "code_change"
    requirement = "requirement"
    test = "test"
    test_result = "test_result"
    repair_attempt = "repair_attempt"
    deployment = "deployment"
    infra_plan = "infra_plan"


class TestKind(StrEnum):
    unit = "unit"
    e2e = "e2e"


class CriterionKind(StrEnum):
    """How an acceptance criterion is intended to be verified (phase-25 → test-gen hint)."""

    unit = "unit"
    e2e = "e2e"
    either = "either"


class TestEnv(StrEnum):
    sandbox = "sandbox"
    live = "live"


class RepairOutcome(StrEnum):
    fixed = "fixed"
    no_progress = "no_progress"
    regressed = "regressed"


class DeployMode(StrEnum):
    seamless = "seamless"
    byo = "byo"


class SettingCategory(StrEnum):
    """Sections of the admin config panel (phase-51/52).

    Every ``Settings`` field belongs to exactly one, so the dashboard can present the *whole*
    environment — not a subset — grouped the way an operator thinks about it.
    """

    models = "models"  # LLM provider, model ids, base URLs, call tuning
    agents = "agents"  # agent loop / tooling budgets
    budget = "budget"  # spend caps + per-model pricing
    providers = "providers"  # design providers (Stitch / Figma) + design intake
    sandbox = "sandbox"  # container limits + command execution
    preview = "preview"  # live preview servers + docker networks
    testing = "testing"  # test runner + live validation
    repair = "repair"  # repair-loop thresholds (phase-51 admin config)
    deploy = "deploy"  # deploy providers, infra analyzer, DB provisioning
    storage = "storage"  # artifacts, blobs, workspace file caps
    database = "database"  # control-plane + app-data Mongo wiring
    realtime = "realtime"  # WebSocket hub tuning
    auth = "auth"  # sessions, rate limits, request caps, seeded accounts
    core = "core"  # environment, version, CORS, logging
    feature_flags = "feature_flags"
