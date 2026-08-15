from __future__ import annotations

from datetime import datetime
from typing import ClassVar

import pymongo
from beanie import Document, PydanticObjectId
from pydantic import Field
from pymongo import IndexModel

from app.db.models.common import utcnow
from app.db.models.enums import ProjectStatus, Stage


class Project(Document):
    user_id: PydanticObjectId
    name: str
    # Requirements is the entry stage (2026-08-06 reorder): knowing what the app must do comes
    # before designing its UI. Non-linearity is unchanged — any stage is still enterable directly.
    current_stage: Stage = Stage.requirements
    status: ProjectStatus = ProjectStatus.active
    stack: str = "fixed-mern-ts"
    sandbox_id: str | None = None
    # Per-project design provider override; None → the admin/env/default provider (phase-16).
    # Lets one project pin Figma when Stitch's quota is exhausted, without affecting others (D10).
    design_provider: str | None = None
    # The freeform description first used to draft requirements — set once, never overwritten, so
    # it stays "the original" even if requirements are later re-drafted from different words. Lets
    # Design (and anything else that wants "what is this app, in the user's own words") generate
    # without making the user retype an idea they already gave ForgeFlow once.
    original_prompt: str | None = None
    app_db_name: str
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    class Settings:
        name = "projects"
        indexes: ClassVar[list[IndexModel]] = [
            IndexModel(
                [("user_id", pymongo.ASCENDING), ("created_at", pymongo.DESCENDING)],
                name="proj_user_created",
            ),
        ]
