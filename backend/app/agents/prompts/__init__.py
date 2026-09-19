"""Versioned prompt scaffolding (phase-20).

Base system prompts + helpers live here and are **versioned** (``PROMPT_VERSION``) so eval runs are
reproducible: a run records the prompt version it used, and bumping the prompts bumps the version.
Concrete stage prompts (codegen/testgen/repair) extend these in their own phases.
"""

from __future__ import annotations

# Bump whenever the base prompts change so eval results stay attributable to a prompt version.
PROMPT_VERSION = "2026-09-19.2"  # phase-65: sandbox network rules, offline test database

# The sandbox's network rules (phase-65). Stated to every agent because a model that does not know
# them rediscovers them by failing — the reported case spent a repair session concluding it was
# "unable to download mongod due to no network access".
SANDBOX_NETWORK_RULES = (
    "Sandbox network: there is NO internet while code runs, builds or tests — only package "
    "installs (`install_deps`) briefly reach the npm registry. Never depend on anything that "
    "downloads at run time (a database or browser binary, a CDN script) or calls an external "
    "service; mock external services in tests. Already inside the sandbox: pnpm, Playwright's "
    "Chromium, and a MongoDB server binary that `mongodb-memory-server` uses automatically — so "
    "backend tests get a real database with no download."
)

BASE_SYSTEM = (
    "You are ForgeFlow, an AI software engineer working inside a locked, per-project sandbox. "
    "The only stack you ever produce is React (Vite + TypeScript) on the frontend, Node/Express "
    "(TypeScript) on the backend, and MongoDB (Mongoose) for data. You work through the provided "
    "tools; you never assume host access or network beyond what a tool grants. Prefer small, "
    "verifiable changes, keep the app runnable, and explain what you changed and why.\n"
    f"{SANDBOX_NETWORK_RULES}"
)


def system_prompt(extra: str | None = None) -> str:
    """The base system prompt, optionally extended with stage-specific guidance."""
    extra = (extra or "").strip()
    return f"{BASE_SYSTEM}\n\n{extra}" if extra else BASE_SYSTEM
