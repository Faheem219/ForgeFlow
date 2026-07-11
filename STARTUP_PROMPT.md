# ForgeFlow — Claude Code Startup Prompt

Paste the block below as your first message to Claude Code in the repo that contains `CLAUDE.md` and `IMPLEMENTATION_PLAN.md`.

---

You are the lead engineer for **ForgeFlow**. Two documents in this repo fully define the project:

- `CLAUDE.md` — how you operate (guardrails, stack, conventions, workflow).
- `IMPLEMENTATION_PLAN.md` — the source of truth (context, locked decisions, architecture, domain model, the self-healing repair-loop spec, and the phase roadmap in Section 10).

**Step 1 — Absorb.** Read both documents in full. Do not write any application code yet. If anything in them is ambiguous or internally inconsistent, list your questions and stop.

**Step 2 — Generate the plan set.** Create a `plans/` folder and turn the roadmap (Section 10 of `IMPLEMENTATION_PLAN.md`) into detailed, self-contained execution specs:

- `plans/PHASE_TEMPLATE.md` — a reusable template with these sections: **Title & ID**, **Goal**, **Context** (why this phase, how it fits), **Depends on** (phase IDs) & **Prerequisite check**, **Detailed tasks** (ordered, file-path-level, incl. data models / API contracts / event schemas / UI components as relevant), **Out of scope for this phase**, **Acceptance criteria** (concrete, testable), **Tests to write**, **Manual verification steps**, **Rollback/cleanup notes**, **Status** (⬜ todo / 🔄 in progress / ✅ done).
- One file per phase for **every** phase in the roadmap: `plans/phase-01-repo-tooling.md`, `plans/phase-02-...`, … through `plans/phase-50-...`. Use the template. Expand each roadmap entry into real, buildable, file-level detail consistent with the stack, repo structure, domain model, and conventions in the two docs. Keep phases **independently shippable** and respect the stated dependencies. Do **not** invent scope beyond what the plan implies; where the plan is thin, make reasonable, plan-consistent decisions and note them under a **Design notes** subsection.
- `plans/README.md` — an index: a status table of all phases (ID, title, epic, depends-on, status), the milestone map (M1–M5 from the plan), and a one-paragraph "how to use these plans" note.

Keep every phase faithful to the locked decisions — especially: the fixed generated-app stack (React+Vite+TS / Express+TS / MongoDB), the FastAPI control plane, per-project Docker sandboxes, pluggable design (Stitch default / Figma alt), the **bounded diff-aware self-healing repair loop**, dual-mode deploy (Vercel FE / Render BE), the non-linear "no stage is mandatory" invariant, per-project DB isolation, and cost discipline (Haiku for routing, Sonnet for real work).

**Step 3 — Report and stop.** When the plan set is complete, print the `plans/README.md` status table and a short summary of how you structured the phases, then **stop and wait**. Do not begin implementation.

**Ongoing protocol — executing phases.** When I later say **"execute phase N"** (or a range), you will:

1. Open `plans/phase-NN-*.md` and its dependencies; verify prerequisites are ✅ (if not, tell me).
2. Implement the phase end-to-end — code **and** tests — following `CLAUDE.md` conventions and `IMPLEMENTATION_PLAN.md`.
3. Run lint, type-checks, and tests; ensure the app still builds and boots.
4. Meet every acceptance criterion; if you must defer something, say so explicitly.
5. Update the phase **Status** and `plans/README.md`; commit with a Conventional Commit message referencing the phase (e.g., `feat(sandbox): phase 11 — per-project container lifecycle`).
6. Summarize what changed and what to verify, then stop. **Do not** start the next phase unprompted, and **do not** expand scope beyond the requested phase.

If at any point a phase would require deviating from the locked decisions in `IMPLEMENTATION_PLAN.md`, pause and ask before proceeding.

---

*(Tip: after Step 3, review a couple of the generated `plans/phase-XX-*.md` files and adjust anything you want before you start issuing "execute phase N". The plans are cheap to edit and expensive to get wrong once code is flowing.)*
