"""Codegen prompt contract (phase-54): the model is told to own the product surface, and the
existing-code block is scoped to FEATURE code so scaffold is never mistaken for prior work.

Pure string assertions — no DB, no model.
"""

from __future__ import annotations

from app.agents.prompts.codegen import CODEGEN_SYSTEM_EXTRA, format_existing

# The six placeholder files the system prompt must name as "overwrite, not part of the product".
_PLACEHOLDER_FILES = (
    "frontend/src/pages/HomePage.tsx",
    "frontend/src/routes.tsx",
    "frontend/src/components/Layout.tsx",
    "frontend/index.html",
    "frontend/src/App.test.tsx",
    "frontend/e2e/home.spec.ts",
)


def test_system_prompt_names_every_placeholder_file() -> None:
    for rel in _PLACEHOLDER_FILES:
        assert rel in CODEGEN_SYSTEM_EXTRA, f"system prompt must name placeholder {rel}"


def test_system_prompt_states_the_no_template_bar() -> None:
    # The exact demo strings that must not survive into a generated app.
    assert "Your app starts here" in CODEGEN_SYSTEM_EXTRA
    assert "Learn Vite" in CODEGEN_SYSTEM_EXTRA
    assert "ForgeFlow" in CODEGEN_SYSTEM_EXTRA
    # Overwrite, never delete — deleting the demo tests breaks `vitest run`.
    assert "OVERWRITTEN" in CODEGEN_SYSTEM_EXTRA
    assert "never deleted" in CODEGEN_SYSTEM_EXTRA


def test_format_existing_none_is_a_first_build() -> None:
    assert format_existing(None) == (
        "Existing feature code: none — this is the first build on a bare skeleton."
    )


def test_format_existing_scopes_the_already_done_wording_to_feature_code() -> None:
    block = format_existing("  backend/src/features/todo/todo.model.ts (12 B)")

    # Still carries the marker the inventory parser keys on…
    assert "ALREADY ON DISK" in block
    # …now framed as a selective repository map of FEATURE code, with scaffold explicitly excluded…
    assert "FEATURE code" in block
    assert "scaffold is NOT listed" in block
    # …and still tells the model plainly not to rewrite correct files or duplicate existing ones.
    assert "Do NOT rewrite" in block
    assert "creating a duplicate" in block
