"""Test-generation prompts (phase-27).

Three prompt surfaces, all extending the versioned base system prompt
(``app.agents.prompts.system_prompt``):

- a cheap **plan** step (Haiku) that turns the requirements spec into a short test plan,
- the **author** step (Sonnet) that writes runnable unit/e2e tests onto the skeleton, and
- an **infer** step (Haiku) that derives a *minimal* spec from design/build artifacts when the
  requirements stage was skipped (D12) — the proposals are labeled inferred, never silently trusted.

The load-bearing convention is **criterion-id tagging**: every generated test embeds the exact
``AcceptanceCriterion.id`` it exercises, so the runner (phase-28) and the repair loop (phase-29)
can map a failing test back to the requirement it verifies. No I/O here — the agent owns wiring.
"""

from __future__ import annotations

from app.db.models.requirement import Feature

# System guidance for the plan step — routed to MODEL_ROUTING (Haiku).
TESTGEN_PLAN_SYSTEM_EXTRA = (
    "You are planning a test suite, not writing it. Read the requirements spec and produce a SHORT "
    "plan (under ~200 words): for each acceptance criterion, which ONE file will cover it and "
    "whether it is a unit test (Vitest for the frontend, Jest+supertest for the backend) or a "
    "Playwright e2e test. Prefer a unit test when a criterion is marked `unit` or `either`; use "
    "Playwright only for `e2e` (user-visible flows). Every criterion must be covered at least once."
)

# System guidance for the author step — routed to MODEL_CODEGEN (Sonnet). Encodes the skeleton test
# conventions AND the mandatory criterion-id tagging that makes the repair loop's traceability work.
TESTGEN_SYSTEM_EXTRA = (
    "You write runnable tests onto an ALREADY-INSTANTIATED fixed-stack skeleton (a pnpm workspace "
    "with `frontend/` React+Vite+TS+Tailwind and `backend/` Express+TS+Mongoose). The toolchain is "
    "already configured — NEVER touch build/test config, and NEVER rescaffold.\n"
    "Test conventions (follow EXACTLY):\n"
    "- Backend unit (Jest + supertest): `backend/src/features/<feature>/<feature>.test.ts`. Build "
    "the app with `createApp()` from `../../app` and drive routes with supertest.\n"
    "- Backend DATA (mandatory): any criterion that touches data — a model, persistence, a query, "
    "or a route that reads or writes — MUST run against the real test database. Add "
    "`import { useTestDb } from '../../test/db'` and call `useTestDb()` ONCE at the top of the "
    "file: it connects Mongoose to an in-memory MongoDB, empties every collection between tests "
    "and drops the database afterwards, fully offline. Do NOT mock Mongoose for these criteria, do "
    "NOT use `mongodb-memory-server` yourself, and do NOT connect to `MONGODB_URI` or any other "
    "database. Only pure logic (no model, no query) may skip the database. (If "
    "`backend/src/test/db.ts` does not exist, the workspace predates the helper: mock Mongoose "
    "for those criteria as before — do not install or hand-roll a database.)\n"
    "- Frontend unit (Vitest + Testing Library): "
    "`frontend/src/features/<feature>/<name>.test.tsx`. "
    "Import from `vitest` and `@testing-library/react`.\n"
    "- E2E (Playwright): `frontend/e2e/<feature>.spec.ts`. Import from `@playwright/test`; "
    "navigate with `page.goto('/')` (the config sets the base URL) and assert on visible text.\n"
    "- No test may call a service on the internet — the sandbox has none; mock it instead.\n"
    "Mapping: a criterion marked `unit` → a unit test; `e2e` → a Playwright spec; `either` → a "
    "unit test (cheaper and more deterministic). Write ONE or more tests per criterion; every "
    "criterion MUST get at least one test.\n"
    "TRACEABILITY (mandatory — this is load-bearing, not cosmetic): in every test file, embed the "
    "EXACT criterion id string(s) it covers, both as a header comment `// @criteria: <id>, <id>` "
    "and as a prefix in each test's title, e.g. `it('[<id>] rejects an empty title', ...)`. Use "
    "the ids verbatim from the spec below — a test without its criterion id cannot be traced to a "
    "requirement.\n"
    "Do NOT run the tests or start a preview — running and parsing results is a separate stage. "
    "Write the files, then commit once with `git_commit`, and finish with a one-line summary."
)

# System guidance for the infer step — routed to MODEL_ROUTING (Haiku). Best-effort, editable.
INFER_SYSTEM = (
    "The requirements stage was skipped. Infer a MINIMAL but sensible requirements spec from the "
    "design and build context provided. Reply with ONLY a JSON array of features and nothing else: "
    '[{"name": string, "description": string, "acceptance_criteria": '
    '[{"text": string, "kind": "unit"|"e2e"|"either"}]}]. Keep it small (1–3 features, a few '
    'criteria each). Use "unit" for logic, "e2e" for user-visible flows, "either" if unsure.'
)


def format_spec(features: list[Feature]) -> str:
    """Render the spec with each criterion's exact id + kind so the author can tag tests."""
    blocks: list[str] = []
    for feature in features:
        parts = [f"### {feature.name}"]
        if feature.description:
            parts.append(feature.description)
        if feature.acceptance_criteria:
            parts.append("Acceptance criteria (tag each test with its id):")
            parts += [f"- id={c.id} kind={c.kind} — {c.text}" for c in feature.acceptance_criteria]
        else:  # pragma: no cover - the requirements service guarantees ≥1 criterion per feature
            parts.append("(no acceptance criteria)")
        blocks.append("\n".join(parts))
    return "\n\n".join(blocks)


def plan_user_prompt(project_name: str, spec: str) -> str:
    return (
        f"Project: {project_name}\n\n"
        f"Requirements spec:\n{spec}\n\n"
        "Produce the short test plan."
    )


def author_user_prompt(project_name: str, plan: str, spec: str, notes: list[str]) -> str:
    notes_block = ("\n\nNotes:\n" + "\n".join(f"- {n}" for n in notes)) if notes else ""
    return (
        f"Project: {project_name}\n\n"
        f"Test plan:\n{plan}\n\n"
        f"Requirements spec (tag every test with the exact criterion id):\n{spec}"
        f"{notes_block}\n\n"
        "Write the tests onto the skeleton now, then commit. Do not run them."
    )


def infer_user_prompt(project_name: str, design: str | None, build: str | None) -> str:
    design_block = f"Design:\n{design}" if design else "Design: none provided."
    build_block = f"Build:\n{build}" if build else "Build: none provided."
    return (
        f"Project: {project_name}\n\n"
        f"{design_block}\n\n"
        f"{build_block}\n\n"
        "Infer the minimal requirements spec as JSON."
    )
