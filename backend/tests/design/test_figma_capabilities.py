"""Figma reports capabilities truthfully and degrades unsupported ops cleanly (no fake result)."""

from __future__ import annotations

import pytest

from app.core.errors import ProviderError
from app.design.base import DesignImage
from app.design.figma import FigmaDesignProvider
from app.design.figma_errors import FigmaErrorKind
from tests.design.test_figma_mock import FakeFigmaClient


def test_capabilities_report_no_screenshot_to_ui() -> None:
    caps = FigmaDesignProvider(client=FakeFigmaClient()).capabilities()

    assert caps.provider == "figma"
    assert caps.from_image is False  # Figma has no screenshot→UI
    assert caps.max_images == 0
    # …but the operations it does support are advertised truthfully.
    assert caps.from_text is True
    assert caps.refine is True
    assert caps.fetch_code is True


async def test_generate_from_image_degrades_clearly_without_faking() -> None:
    client = FakeFigmaClient()
    provider = FigmaDesignProvider(client=client)
    images = [DesignImage(filename="shot.png", media_type="image/png", data=b"\x89PNG")]

    with pytest.raises(ProviderError) as excinfo:
        await provider.generate_from_image(images)

    err = excinfo.value
    assert isinstance(err.detail, dict) and err.detail["kind"] == FigmaErrorKind.unsupported
    assert err.fallback_hint  # points the user at a supported input / provider
    # It degraded *before* touching the transport — no fabricated success.
    assert client.calls == []
