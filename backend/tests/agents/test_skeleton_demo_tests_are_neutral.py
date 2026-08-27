"""The skeleton's example tests must not assert the placeholder page (root-cause guard).

`build_verify` FAILS a build whose app still contains the demo copy, so any shipped test that
asserts that copy is unsatisfiable the moment a real app is generated — and `is_test_file` bars the
repair loop from editing a test to resolve it. A real project burned its entire repair budget on
exactly that deadlock.

`_DEMO_TEST_FILES` is the runtime guard for a *generated* workspace; this is the guard for the
template those workspaces are copied from, so the contradiction cannot be reintroduced at source.
Runs against the real `templates/app-skeleton`.
"""

from __future__ import annotations

import pytest

from app.agents.tools.skeleton import skeleton_source_dir
from app.orchestrator.stages.build_verify import _DEMO_TEST_FILES, _PLACEHOLDER_STRINGS


@pytest.mark.parametrize("relpath", _DEMO_TEST_FILES)
def test_shipped_demo_test_asserts_no_placeholder_copy(relpath: str) -> None:
    path = skeleton_source_dir() / relpath
    assert path.is_file(), f"skeleton demo test missing at {relpath}"

    lowered = path.read_text(encoding="utf-8").lower()
    found = [needle for needle in _PLACEHOLDER_STRINGS if needle.lower() in lowered]

    assert not found, (
        f"{relpath} asserts placeholder copy {found} that the build gate forbids — it can never "
        "pass against a generated app, and repair may not edit test files to fix it."
    )


def test_the_placeholder_page_itself_still_carries_the_copy() -> None:
    """The inverse: the demo PAGE is meant to keep it — that is what codegen must overwrite, and
    what the placeholder gate greps for. Without this the test above passes trivially if the
    skeleton's placeholder were simply deleted."""
    home = skeleton_source_dir() / "frontend/src/pages/HomePage.tsx"

    assert any(needle in home.read_text(encoding="utf-8") for needle in _PLACEHOLDER_STRINGS)
