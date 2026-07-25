"""Codegen prompts (phase-23).

Two prompt surfaces: a cheap **plan** step (Haiku) that turns design + requirements into a short
build plan, and the **implement** step (Sonnet) that writes feature code onto the
already-instantiated skeleton. Both extend the versioned base system prompt
(``app.agents.prompts.system_prompt``). No I/O here — the codegen agent owns model/tool wiring.
"""

from __future__ import annotations

# System guidance for the plan step — routed to MODEL_ROUTING (Haiku).
PLAN_SYSTEM_EXTRA = (
    "You are planning a build, not writing code. Read the design and requirements context and "
    "produce a SHORT, concrete plan (under ~200 words): the backend models/routes and the frontend "
    "pages/components to add onto the existing skeleton. If context is missing, plan a minimal but "
    "working feature and state what you assumed."
)

# System guidance for the implement step — routed to MODEL_CODEGEN (Sonnet). Encodes the skeleton
# conventions so the agent writes ONLY feature code and never rescaffolds (the token-saving lever).
CODEGEN_SYSTEM_EXTRA = (
    "You implement features onto an ALREADY-INSTANTIATED fixed-stack skeleton — a pnpm workspace "
    "with `frontend/` (React + Vite + TypeScript + Tailwind) and `backend/` (Express + TypeScript "
    "+ Mongoose + Zod). The scaffold is done for you: NEVER rescaffold or edit build config, "
    "Tailwind, the API client, the Express app factory, or middleware except to register a route.\n"
    "Backend: add `<feature>.model.ts` / `.schema.ts` / `.controller.ts` / `.routes.ts` under "
    "`backend/src/features/<feature>/`; mount the router in `backend/src/app.ts` at the "
    "`FORGEFLOW:ROUTES` marker; validate request bodies with Zod via `lib/validate`; throw "
    "`AppError` for expected client errors.\n"
    "Frontend: add pages/components under `frontend/src/features/<feature>/`; register routes in "
    "`frontend/src/routes.tsx` at the `FORGEFLOW:ROUTES` marker; call the backend ONLY through the "
    "typed client in `frontend/src/lib/api.ts`. Style with Tailwind utility classes; turn any "
    "provided design HTML/CSS into idiomatic React + Tailwind components, not raw HTML.\n"
    "Workflow: write the code, install any NEW dependencies, then start the preview and make both "
    "servers healthy — fix boot errors as they arise, keeping changes small and verifiable. Commit "
    "with `git_commit` once it builds and boots. Finish with a concise summary of the features you "
    "built and any follow-ups.\n"
    "Resuming: a build may run against a workspace an earlier build already wrote to (it may have "
    "failed part-way). Never restart from scratch when feature code is already listed as present — "
    "read what exists, keep what is correct, and write only the missing or incomplete parts."
)


def format_design(summary: str | None) -> str:
    if not summary:
        return "Design: none provided — build a clean, minimal Tailwind UI."
    return f"Design (map into React + Tailwind components):\n{summary}"


def format_requirements(summary: str | None) -> str:
    if not summary:
        return "Requirements: none provided — infer a minimal, sensible feature set."
    return f"Requirements:\n{summary}"


def format_existing(inventory: str | None) -> str:
    """The existing-feature-code block: the lever that makes a rebuild incremental.

    Without it the model cannot tell a fresh workspace from one a previous (possibly failed) build
    already populated, so it regenerates everything — the waste this block exists to stop.
    """
    if not inventory:
        return "Existing feature code: none — this is the first build on a bare skeleton."
    return (
        "Existing feature code (from earlier builds — ALREADY ON DISK):\n"
        f"{inventory}\n"
        "Treat this as work already done. Do NOT rewrite a file that is already correct: use "
        "`read_file` to check anything you intend to change, then write ONLY what is missing, "
        "incomplete, or required by the request above. Finishing a half-built feature is the goal."
    )


def plan_user_prompt(
    project_name: str,
    design: str | None,
    requirements: str | None,
    existing: str | None = None,
) -> str:
    return (
        f"Project: {project_name}\n\n"
        f"{format_requirements(requirements)}\n\n"
        f"{format_design(design)}\n\n"
        f"{format_existing(existing)}\n\n"
        "Produce the short build plan. If feature code already exists, plan only the remaining "
        "work — list what is already done separately from what is left."
    )


def implement_user_prompt(
    project_name: str,
    plan: str,
    design: str | None,
    requirements: str | None,
    notes: list[str],
    existing: str | None = None,
) -> str:
    notes_block = ("\n\nNotes:\n" + "\n".join(f"- {n}" for n in notes)) if notes else ""
    return (
        f"Project: {project_name}\n\n"
        f"Build plan:\n{plan}\n\n"
        f"{format_requirements(requirements)}\n\n"
        f"{format_design(design)}\n\n"
        f"{format_existing(existing)}"
        f"{notes_block}\n\n"
        "Implement the remaining plan onto the skeleton now, then verify it boots."
    )
