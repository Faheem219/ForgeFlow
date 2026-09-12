from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import ClassVar

import pymongo
from beanie import Document, PydanticObjectId
from pydantic import Field
from pymongo import IndexModel

from app.db.models.common import utcnow


class DesignQuestionStatus(StrEnum):
    pending = "pending"  # waiting on the user — the design stage is awaiting_user
    answered = "answered"  # the user replied and the answer was sent back to the provider
    dismissed = "dismissed"  # superseded by a new intake, or the user chose to design without it


class DesignQuestion(Document):
    """A design provider's clarifying question, parked until the user answers it.

    Stitch answers a broad brief with a scope proposal and a question ("shall I proceed with these
    five screens?") as often as it answers with a design. The transport confirms the first few
    itself (``STITCH_CLARIFY_MAX_REPLIES``), but a question it cannot answer blindly — *who* is the
    app for, *which* half of the scope first — used to end as a provider failure and drop the whole
    design onto the figma/fake chain. Recording it instead turns that dead end into a turn of the
    conversation the user can actually take.

    Everything needed to resume the call lives here, because the generate produced no artifact to
    read it back from: the brief that triggered the question, the provider that asked, and the
    provider-side container that call created (so the answer lands in the same Stitch project
    rather than opening a second one).
    """

    project_id: PydanticObjectId
    #: The provider that asked. The answer goes back to *it*, not to the active provider — the
    #: same provenance rule refines follow (see ``resilient.provider_for_refine``).
    provider: str
    question: str
    #: Quick replies the provider offered alongside the question, when it offered any.
    suggestions: list[str] = Field(default_factory=list)
    #: The brief that triggered the question — re-sent with the answer, since the provider's tools
    #: are stateless and the answer cannot be threaded onto the conversation that asked.
    prompt: str = ""
    #: The provider-side container the asking call created (Stitch: its project id), so the
    #: answered generate lands in the same place.
    workspace: str = ""
    #: What the user was doing: ``text`` (generate) or ``refine``. Decides which provider call the
    #: answer resumes.
    source: str = "text"
    #: The screen a refine targeted, when ``source`` is ``refine``.
    design_ref: str = ""
    status: DesignQuestionStatus = DesignQuestionStatus.pending
    answer: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    class Settings:
        name = "design_questions"
        indexes: ClassVar[list[IndexModel]] = [
            IndexModel(
                [
                    ("project_id", pymongo.ASCENDING),
                    ("status", pymongo.ASCENDING),
                    ("created_at", pymongo.DESCENDING),
                ],
                name="dq_project_status_created",
            ),
        ]
