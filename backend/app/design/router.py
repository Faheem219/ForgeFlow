"""Design stage HTTP surface (phase-19): screenshot upload + active-provider info.

Design *versions* and their HTML/CSS are read through the existing artifact endpoints
(``GET /projects/{id}/artifacts?type=design`` and ``GET /artifacts/{id}``); generate/refine/approve/
skip go through the conductor (``POST /projects/{id}/intent``). This router only adds what those
don't cover: uploading screenshots to the blob store, and reporting the active provider's health +
capabilities for the UI indicators.
"""

from __future__ import annotations

import base64
import binascii
import logging

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends

from app.auth.deps import get_current_user_id
from app.core.config import get_config
from app.core.errors import ProviderError, UserError
from app.core.limits import expensive_rate_limit
from app.db.blobs import get_blob_store
from app.design.base import ScreenLister
from app.design.registry import get_active, resolve_active_key
from app.design.schemas import (
    DesignImageRef,
    DesignProviderInfo,
    ImageUploadRequest,
    ImageUploadResponse,
    ScreenCodeResponse,
    ScreenListResponse,
)
from app.projects.service import ProjectService, parse_object_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects", tags=["design"])


@router.post(
    "/{project_id}/design/images",
    response_model=ImageUploadResponse,
    dependencies=[Depends(expensive_rate_limit)],
)
async def upload_design_images(
    project_id: str,
    body: ImageUploadRequest,
    user_id: PydanticObjectId = Depends(get_current_user_id),
) -> ImageUploadResponse:
    """Store screenshots (base64 JSON) in the blob layer; the intent then references their refs."""
    pid = parse_object_id(project_id)
    await ProjectService().get_owned(pid, user_id)

    config = get_config()
    max_images = int(config.get("design_max_images"))
    max_bytes = int(config.get("design_max_image_bytes"))

    if not body.images:
        raise UserError("No images provided")
    if len(body.images) > max_images:
        raise UserError(f"Too many images (max {max_images})")

    blobs = get_blob_store()
    stored: list[DesignImageRef] = []
    for image in body.images:
        try:
            data = base64.b64decode(image.data_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise UserError(f"Invalid base64 for {image.filename}") from exc
        if len(data) > max_bytes:
            raise UserError(f"{image.filename} exceeds the {max_bytes}-byte limit")
        ref = await blobs.put(data)
        stored.append(DesignImageRef(ref=ref, filename=image.filename, media_type=image.media_type))

    return ImageUploadResponse(images=stored)


@router.get("/{project_id}/design/screens", response_model=ScreenListResponse)
async def list_design_screens(
    project_id: str,
    user_id: PydanticObjectId = Depends(get_current_user_id),
) -> ScreenListResponse:
    """Every screen in the active provider's project — the whole app, not just the latest version.

    A design version records only the screen that turn produced, so a multi-screen app (landing,
    login, dashboard…) is spread across versions. This lets the UI show them all in one picker.
    Empty when the provider cannot enumerate screens (`fake`, an imported design), so the UI just
    hides the picker.
    """
    pid = parse_object_id(project_id)
    project = await ProjectService().get_owned(pid, user_id)

    provider = get_active(project)
    if not isinstance(provider, ScreenLister):
        return ScreenListResponse()
    try:
        return ScreenListResponse(screens=await provider.list_screens())
    except ProviderError:
        # A picker is a convenience — never fail the design stage because it could not be filled.
        logger.warning("design: could not list screens", exc_info=True)
        return ScreenListResponse()


@router.get("/{project_id}/design/screen", response_model=ScreenCodeResponse)
async def get_design_screen(
    project_id: str,
    ref: str,
    user_id: PydanticObjectId = Depends(get_current_user_id),
) -> ScreenCodeResponse:
    """One screen's markup, fetched on demand when the user picks it in the preview."""
    pid = parse_object_id(project_id)
    project = await ProjectService().get_owned(pid, user_id)

    code = await get_active(project).fetch_code(ref)
    return ScreenCodeResponse(ref=ref, html=code.html, css=code.css)


@router.get("/{project_id}/design/provider", response_model=DesignProviderInfo)
async def get_design_provider(
    project_id: str,
    user_id: PydanticObjectId = Depends(get_current_user_id),
) -> DesignProviderInfo:
    """Active provider (honoring the per-project override) + its health & capabilities."""
    pid = parse_object_id(project_id)
    project = await ProjectService().get_owned(pid, user_id)

    provider = get_active(project)
    return DesignProviderInfo(
        key=resolve_active_key(project),
        health=await provider.health(),
        capabilities=provider.capabilities(),
    )
