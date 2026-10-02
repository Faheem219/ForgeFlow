"""The skeleton's e2e suite must fit the sandbox: one Playwright worker.

Playwright sizes its worker pool from the host's CPU count, not the sandbox's CPU cap, and each
Chromium costs ~100 threads against the sandbox pid limit. Three workers crashed every browser at
launch, so every e2e criterion failed for an environment reason and the repair loop (correctly)
refused to touch the code. This pins the fix in the template every generated app is copied from.
"""

from __future__ import annotations

import re
from pathlib import Path

CONFIG = (
    Path(__file__).resolve().parents[3] / "templates" / "app-skeleton" / "frontend"
    / "playwright.config.ts"
)  # fmt: skip


def test_the_skeleton_runs_one_playwright_worker() -> None:
    text = CONFIG.read_text(encoding="utf-8")
    assert re.search(r"^\s*workers:\s*1,", text, re.M), "skeleton must pin `workers: 1`"
