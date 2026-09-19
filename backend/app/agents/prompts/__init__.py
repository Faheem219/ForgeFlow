"""Versioned prompt scaffolding (phase-20).

Base system prompts + helpers live here and are **versioned** (``PROMPT_VERSION``) so eval runs are
reproducible: a run records the prompt version it used, and bumping the prompts bumps the version.
Concrete stage prompts (codegen/testgen/repair) extend these in their own phases.
"""

from __future__ import annotations

# Bump whenever the base prompts change so eval results stay attributable to a prompt version.
PROMPT_VERSION = "2026-09-19.1"  # phase-64: truncation nudge, no-op feedback, summary nudge

BASE_SYSTEM = (
    "You are ForgeFlow, an AI software engineer working inside a locked, per-project sandbox. "
    "The only stack you ever produce is React (Vite + TypeScript) on the frontend, Node/Express "
    "(TypeScript) on the backend, and MongoDB (Mongoose) for data. You work through the provided "
    "tools; you never assume host access or network beyond what a tool grants. Prefer small, "
    "verifiable changes, keep the app runnable, and explain what you changed and why."
)


def system_prompt(extra: str | None = None) -> str:
    """The base system prompt, optionally extended with stage-specific guidance."""
    extra = (extra or "").strip()
    return f"{BASE_SYSTEM}\n\n{extra}" if extra else BASE_SYSTEM
