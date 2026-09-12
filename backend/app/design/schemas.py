"""Design stage API + intake schemas (phase-19).

The Intent ``payload`` for the design stage is one of: a text prompt, uploaded screenshot refs, or
a bring-your-own design — parsed by :class:`DesignIntake`. Screenshots are uploaded first (base64
JSON, so no multipart dependency) to the blob store; the intake then references their blob refs.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.db.models import DesignQuestion
from app.design.base import DesignCapabilities, DesignScreenRef, ProviderHealth


class DesignImageRef(BaseModel):
    """A screenshot already stored in the blob layer (returned by the upload endpoint)."""

    ref: str
    filename: str
    media_type: str


class OwnDesign(BaseModel):
    """A bring-your-own design: stored as a ``design`` artifact with no provider call."""

    html: str
    css: str = ""


class DesignIntake(BaseModel):
    """Parsed from ``intent.payload`` for design (all fields optional; the handler validates)."""

    text: str | None = None
    image_refs: list[DesignImageRef] = Field(default_factory=list)
    own_design: OwnDesign | None = None
    #: Which screen a refine targets. Blank → the latest version's own screen. Set by the UI's
    #: screen picker so "make this darker" changes the screen the user is actually looking at,
    #: not whichever one happened to be generated last.
    design_ref: str | None = None
    #: Close the provider's pending question unanswered and design without it — the escape hatch
    #: for a user who would rather have *a* design than answer ("Design without Stitch"). The
    #: asking provider is excluded and the fallback chain serves the request instead.
    dismiss_question: bool = False


class DesignQuestionPublic(BaseModel):
    """The design provider's pending question, for the stage UI to put to the user."""

    id: str
    provider: str
    question: str
    #: Quick replies the provider offered, when it offered any — rendered as one-click answers.
    suggestions: list[str] = Field(default_factory=list)
    #: ``text`` (a generate) or ``refine`` — what the answer will resume.
    source: str
    created_at: datetime

    @classmethod
    def from_question(cls, question: DesignQuestion) -> DesignQuestionPublic:
        return cls(
            id=str(question.id),
            provider=question.provider,
            question=question.question,
            suggestions=question.suggestions,
            source=question.source,
            created_at=question.created_at,
        )


class UploadImage(BaseModel):
    filename: str
    media_type: str
    data_base64: str


class ImageUploadRequest(BaseModel):
    images: list[UploadImage] = Field(default_factory=list)


class ImageUploadResponse(BaseModel):
    images: list[DesignImageRef]


class DesignProviderInfo(BaseModel):
    """Active-provider snapshot for the design UI's provider/health indicators."""

    key: str
    health: ProviderHealth
    capabilities: DesignCapabilities


class ScreenListResponse(BaseModel):
    """Every screen in the provider's project. Empty when the provider cannot enumerate them."""

    screens: list[DesignScreenRef] = Field(default_factory=list)


class ScreenCodeResponse(BaseModel):
    """One screen's markup, for the preview's screen picker."""

    ref: str
    html: str
    css: str = ""
