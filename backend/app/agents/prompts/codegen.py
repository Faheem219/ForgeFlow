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
    "Tailwind, the API client, the Express app factory, the test database helper under "
    "`backend/src/test/`, or middleware except to register a route.\n"
    "Backend tests that touch data call `useTestDb()` from `backend/src/test/db` (a real, "
    "in-memory MongoDB that works offline) — never `mongodb-memory-server` directly or a real "
    "database URI.\n"
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
    "read what exists, keep what is correct, and write only the missing or incomplete parts.\n"
    "Product surface: the skeleton ships PLACEHOLDER demo files that are NOT part of the product. "
    "Each MUST be OVERWRITTEN (never deleted) so nothing of the template survives into the "
    "finished app: `frontend/src/pages/HomePage.tsx` — replace with the app's real landing page; "
    "`frontend/src/routes.tsx` — the INDEX route must render a real feature page, so replace the "
    "placeholder child, not merely add siblings after the FORGEFLOW:ROUTES marker; "
    "`frontend/src/components/Layout.tsx` — the wordmark must be the app's name; "
    "`frontend/index.html` — the `<title>` must be the app's name; "
    "`frontend/src/App.test.tsx` and `frontend/e2e/home.spec.ts` — REWRITE them in place to assert "
    "real app content (deleting a test file makes the test run exit with 'No test files found', a "
    "false failure). The bar: the finished app contains no `ForgeFlow`, no `Your app starts here`, "
    "and no `Learn Vite`."
)


# System guidance for the phase-PLAN step (phase-56) — routed to MODEL_ROUTING (Haiku), like the
# prose plan it supersedes. Strict JSON: the parser tolerates fences and prose, but asking for
# anything else wastes a Haiku round-trip on text that then falls back.
PHASE_PLAN_SYSTEM_EXTRA = (
    "You are planning a build as a short, ORDERED list of implementation phases. You write no "
    "code here.\n"
    "Reply with STRICT JSON and nothing else — no prose, no commentary — in exactly this shape:\n"
    '{"phases":[{"id":"be-todos","title":"Todo API","kind":"backend","goal":"…",'
    '"files":["backend/src/features/todos/"],"depends_on":[],'
    '"done_when":["backend typechecks","POST /api/todos creates a todo"]}],'
    '"assumptions":["…"],"notes":["…"]}\n'
    "Rules:\n"
    "- `kind` is one of data | backend | frontend | wiring, and phases run in that order: the "
    "data layer first, then the API, then the UI, then the wiring that ties them together.\n"
    "- Each phase must be INDEPENDENTLY TYPECHECKABLE: never plan a frontend page against an API "
    "client written in a later phase.\n"
    "- `goal` is one or two concrete sentences naming what to build. `files` are the paths the "
    "phase will create or edit. `done_when` are short CHECKABLE statements, not aspirations.\n"
    "- `depends_on` holds ids of earlier phases only; never create a cycle.\n"
    "- The FINAL phase is always `wiring`: the index route must render a real feature page, the "
    "layout wordmark and the HTML title must be the app's name, and the placeholder tests must "
    "be rewritten to assert real app content.\n"
    "- Keep the plan small. Fewer, larger phases beat many trivial ones."
)

# System guidance for implementing ONE phase (phase-56) — routed to MODEL_CODEGEN (Sonnet). It
# extends CODEGEN_SYSTEM_EXTRA rather than replacing it: the skeleton conventions still bind.
PHASE_IMPLEMENT_SYSTEM_EXTRA = (
    CODEGEN_SYSTEM_EXTRA
    + "\n"
    + "You are implementing ONE PHASE of a larger plan, not the whole app. Do exactly this "
    "phase's goal and stop — a later phase covers the rest, and work done early is work done "
    "without the context it needs. Do not install dependencies, start the preview or run tests: "
    "the build verifies and boots the app itself once every phase is written. Finish with a "
    "one-line summary of what you wrote."
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
        "Repository map — FEATURE code from earlier builds, ALREADY ON DISK "
        "(paths + sizes; use `read_file` for contents):\n"
        f"{inventory}\n"
        "This is your record of what already exists. The scaffold is NOT listed here and is not "
        "yours to rewrite. Recognise this functionality: modify the file above in place rather "
        "than creating a duplicate, and write ONLY what is missing, incomplete, or required by "
        "the request. Do NOT rewrite a file that is already correct. Finishing a half-built "
        "feature is the goal."
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


def phase_plan_user_prompt(
    project_name: str,
    design: str | None,
    requirements: str | None,
    existing: str | None = None,
    *,
    max_phases: int = 8,
    instruction: str | None = None,
) -> str:
    """The phase-plan step's user turn (phase-56) — the JSON plan the build is then driven by."""
    change = (
        f"\n\nThe user requested this specific change — plan ONLY for it: {instruction}"
        if instruction
        else ""
    )
    return (
        f"Project: {project_name}\n\n"
        f"{format_requirements(requirements)}\n\n"
        f"{format_design(design)}\n\n"
        f"{format_existing(existing)}"
        f"{change}\n\n"
        f"Produce the phase plan as JSON: at least 3 and at most {max_phases} phases. If feature "
        "code already exists, plan only the REMAINING work — do not plan a phase whose goal is "
        "already met."
    )


def phase_user_prompt(
    project_name: str,
    phase_index: int,
    phase_total: int,
    title: str,
    kind: str,
    goal: str,
    files: list[str],
    done_when: list[str],
    story_so_far: str,
    design: str | None,
    requirements: str | None,
    existing: str | None = None,
    notes: list[str] | None = None,
) -> str:
    """One phase's implement turn — a *small* context, which is the whole point of phasing.

    Deliberately not the accumulated transcript of previous phases: those cost tokens on every
    subsequent turn and drown the one goal that matters now. The previous phases appear only as the
    short "story so far", and the design/requirements slice is whichever half this phase touches.
    """
    files_block = "\nFiles this phase owns:\n" + "\n".join(f"- {f}" for f in files) if files else ""
    done_block = "\nDone when:\n" + "\n".join(f"- {d}" for d in done_when) if done_when else ""
    story_block = f"\n\nStory so far: {story_so_far}" if story_so_far else ""
    notes_block = ("\n\nNotes:\n" + "\n".join(f"- {n}" for n in (notes or []))) if notes else ""
    # The requirements are the spec — every phase needs them. The design HTML/CSS is the largest
    # block by far and means nothing to a backend phase, so it goes only to the phases that render.
    # That slice is the cost lever.
    context = format_requirements(requirements)
    if kind in ("frontend", "wiring"):
        context += "\n\n" + format_design(design)
    return (
        f"Project: {project_name}\n"
        f"Phase {phase_index} of {phase_total}: {title} ({kind})\n\n"
        f"Goal: {goal}"
        f"{files_block}"
        f"{done_block}"
        f"{story_block}\n\n"
        f"{context}\n\n"
        f"{format_existing(existing)}"
        f"{notes_block}\n\n"
        "Implement THIS PHASE ONLY, then stop. Write the files, keep them typechecking, and do "
        "not start work that belongs to a later phase."
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
