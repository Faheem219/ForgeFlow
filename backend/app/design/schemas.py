"""Design stage API + intake schemas (phase-19).

The Intent ``payload`` for the design stage is one of: a text prompt, uploaded screenshot refs, or
a bring-your-own design — parsed by :class:`DesignIntake`. Screenshots are uploaded first (base64
JSON, so no multipart dependency) to the blob store; the intake then references their blob refs.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.design.base import DesignCapabilities, ProviderHealth


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
