from __future__ import annotations

from collections.abc import Iterator

import pytest

from app.core.config import reset_config


@pytest.fixture(autouse=True)
def _reset_config() -> Iterator[None]:
    """Ensure each test sees a fresh process-wide config resolver."""
    reset_config()
    yield
    reset_config()
