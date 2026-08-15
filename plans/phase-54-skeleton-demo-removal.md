# Phase 54 — Skeleton demo removal & tool-surface hardening

## Title & ID

- **ID:** `phase-54`
- **Title:** Skeleton demo removal & tool-surface hardening
- **Epic:** Epic 13 — Build Reliability *(added per user directive)*
- **Milestone:** M2 *(hardens M2's stated outcome — a generated **running app**)*
- **Status:** ⬜ todo

## Goal

Stop the skeleton's placeholder product — "Your app starts here", the Learn Vite button, the "ForgeFlow App" wordmark and page title — from surviving into generated apps, and stop the codegen loop from being able to rescaffold itself mid-build.

## Context

The **user directive**: *"I want to tweak the prompt/UI so that the template shown in the image is completely removed/overwritten by the new application. I don't want the template to exist as a starting product or appear in the final app."*

The cause is not the model being careless — it is being told, correctly, to leave those files alone. `_SKELETON_FILES` (`app/agents/codegen.py:75-83`) is a hand-maintained 7-entry literal, but the skeleton ships **17** files under `backend/src` + `frontend/src`. `_existing_code()` (`codegen.py:474-508`) therefore reports ten skeleton files — including `frontend/src/pages/HomePage.tsx` and `components/Layout.tsx` — as *existing feature code*, and `format_existing` (`app/agents/prompts/codegen.py:56-70`) wraps that list in **"Treat this as work already done. Do NOT rewrite a file that is already correct."** On a bare skeleton, on the very first build.

Compounding it, `templates/app-skeleton/frontend/src/routes.tsx:13-14` places `{ index: true, element: <HomePage /> }` *above* the `FORGEFLOW:ROUTES` marker, and the marker adds **siblings** — so `/` keeps rendering the demo page regardless of what the agent registers.

Locked decisions honoured, all of them: the skeleton is still instantiated by **deterministic copy and never model-generated** (the phase-22/23 user directive, `plans/README.md`), the fixed generated-app stack is untouched (D2), context discipline is preserved (§9 — this phase makes the context *smaller*), and no hard prerequisite is added (D12).

**Scope decision:** `templates/app-skeleton/` is **not edited**. Per the user's explicit choice, the placeholder files are overwritten by the agent under prompt guidance rather than deleted from the template. `phase-55` adds a deterministic check so a surviving placeholder becomes a repairable finding instead of a silent regression.

## Depends on

- **Phase IDs:** `phase-22` (app skeleton template), `phase-23` (codegen agent loop), `phase-21` (agent tools).
- **External:** none. No Docker, no model, no provider key — every change here is unit-testable.

### Prerequisite check

- `phase-21`, `phase-22`, `phase-23` are ✅ in `plans/README.md`.
- `templates/app-skeleton/` exists and `skeleton_source_dir()` (`app/agents/tools/skeleton.py:21-24`) resolves it.
- `cd backend && uv run pytest tests/agents -q` is green before starting.

## Detailed tasks

1. **Derive the skeleton path set (`app/agents/tools/skeleton.py`).** Add

   ```python
   @lru_cache(maxsize=4)
   def _relpaths_for(src: str) -> frozenset[str]: ...
   def skeleton_relpaths() -> frozenset[str]:
       """Every path the skeleton ships, derived from the source dir (never hand-maintained)."""
   ```

   using the **same** `rglob("*")` walk and `UnicodeDecodeError` skip as `copy_skeleton` (L27-49), so the two can never disagree. Cached, because it is read once per `_existing_code` call.

2. **Replace `_SKELETON_FILES` (`app/agents/codegen.py:75-83`).** Delete the literal. Add

   ```python
   def _skeleton_paths() -> frozenset[str]:
       try:
           return skeleton_relpaths()
       except UserError:          # skeleton dir missing in an exotic env
           return _SKELETON_FALLBACK   # today's 7-entry literal, kept as a floor
   ```

   and have `_existing_code` (L495) test membership against it. **This is the single highest-leverage line in the phase:** on a freshly instantiated workspace `_existing_code` then returns `(None, [])`, so `format_existing` emits *"Existing feature code: none — this is the first build on a bare skeleton"* and the "do not rewrite" block stops firing on placeholder files.

3. **Narrow the model's tool surface (`app/agents/codegen.py:291`).** The implement loop is handed `self._registry.anthropic_tools()` — all 10 tools, **including `instantiate_skeleton`**, which re-copies the skeleton and silently overwrites `app.ts` / `routes.tsx`, wiping registered routes. Add

   ```python
   _MODEL_EXCLUDED_TOOLS = frozenset({"instantiate_skeleton"})
   def _model_tools(self) -> list[dict[str, Any]]: ...
   ```

   and pass that to `run_tool_loop`. `self._registry` stays the full default registry so the agent's own `dispatch("instantiate_skeleton", {})` at L233 still resolves. Optionally add `ToolRegistry.anthropic_tools(exclude: set[str] | None = None)` (`app/agents/tools/registry.py:74-77`) rather than filtering at the call site — either is fine; pick one and be consistent.

4. **Prompt: the model owns the product surface (`app/agents/prompts/codegen.py`).** Add a paragraph to `CODEGEN_SYSTEM_EXTRA` (L21-41) naming the placeholder files as **not part of the product**, and requiring each be **overwritten**:

   | File | Requirement |
   |---|---|
   | `frontend/src/pages/HomePage.tsx` | replace with the app's real landing page |
   | `frontend/src/routes.tsx` | the **index** route must render a real feature page, not the placeholder |
   | `frontend/src/components/Layout.tsx` | wordmark = the app's name |
   | `frontend/index.html` | `<title>` = the app's name |
   | `frontend/src/App.test.tsx` | rewrite to assert real app content |
   | `frontend/e2e/home.spec.ts` | rewrite to assert real app content |

   State the bar plainly: **the finished app contains no `ForgeFlow`, no `Your app starts here`, no `Learn Vite`.**

   **Overwrite, never delete.** `App.test.tsx` must be *replaced* — removing it makes `vitest run` exit 1 with "No test files found", which `app/testing/runner.py:194` turns into a synthetic suite failure on every subsequent build.

5. **Scope the incremental-resume text (`prompts/codegen.py:56-70`).** `format_existing`'s "already done" wording is correct for feature code and wrong for scaffold. Tighten it so it cannot be misread again: *"the files listed below are FEATURE code from earlier builds. The scaffold is not listed and is not yours to rewrite."*

6. **Bump `PROMPT_VERSION` (`app/agents/prompts/__init__.py:11`).** The prompts changed; the version string is the eval harness's join key.

7. **Repository awareness without context bloat.** The agent must also maintain knowledge of the code files in the workspace using a compact, selective repository map rather than dumping all code files into the LLM context. It should know which files exist, what each file is for, and what has already been implemented so it can recognize existing functionality and avoid creating unnecessary new files that may already exist and only need modification.

## Out of scope for this phase

- Editing `templates/app-skeleton/` in any way — the user chose prompt-only. (The deterministic fallback, if prompt-only proves flaky, is a future phase.)
- A `delete_file` tool. Overwriting the placeholders is sufficient, and deleting the demo tests would break `vitest run` anyway. `WorkspaceApi` (`app/agents/tools/context.py:15-26`) is unchanged.
- The deterministic placeholder **check** that enforces this at verification time — `phase-55`, task B1.4.
- Phasing the build, the self-heal loop, and the completion gate — `phase-55` and `phase-56`.

## Acceptance criteria

- On a freshly instantiated workspace, `_existing_code()` returns `(None, [])` and the implement prompt contains *"this is the first build on a bare skeleton"*.
- `skeleton_relpaths()` is a superset of every file under `templates/app-skeleton/`, asserted against the real template directory — so the set can never silently drift from the skeleton again.
- The tool list handed to the implement loop does **not** contain `instantiate_skeleton`, while `CodegenAgent`'s own step-0 dispatch of it still succeeds.
- `CODEGEN_SYSTEM_EXTRA` names all six placeholder files and states the no-`ForgeFlow`/no-`Your app starts here`/no-`Learn Vite` bar.
- An incremental (second) build still reports genuine feature files from the previous run as existing, and still does not report scaffold — resume behaviour is unchanged.
- `ruff` / `black` / `mypy` clean; the existing agent suite is green.

## Tests to write

**Control plane (`pytest`)**

- `tests/agents/test_skeleton_paths_complete.py` *(new)* — against the **real** `templates/app-skeleton`, not a fixture: `skeleton_relpaths()` ⊇ every file under `{frontend,backend}/src`; `_existing_code()` on a freshly instantiated workspace returns `(None, [])`. **The drift regression guard** — this is the test that would have caught the original bug.
- `tests/agents/test_codegen_tool_surface.py` *(new)* — `_model_tools()` omits `instantiate_skeleton` and retains the other nine; step 0's `dispatch("instantiate_skeleton", {})` still resolves through the full registry.
- `tests/agents/test_codegen_prompts.py` *(new)* — `CODEGEN_SYSTEM_EXTRA` mentions each of the six placeholder paths; `format_existing(None)` says "bare skeleton"; `format_existing(inventory)` carries the tightened FEATURE-code wording.
- **Re-run and adjust if they pin the old literal:** `tests/agents/test_codegen_skeleton_first.py`, `test_codegen_incremental.py`, `test_codegen_nonlinear.py`, `test_codegen_unfinished.py`.

**Frontend** — none. This phase is backend-only.

## Manual verification steps

```bash
cd backend && uv run pytest tests/agents -q
uv run ruff check . && uv run black --check . && uv run mypy .
```

Then, with the stack up (`docker compose -f infra/docker-compose.yml up -d --no-deps proxy appdb`), press **Build** on a **new** project and check:

- The build's first assistant turn does not claim existing feature code (visible in the Build conversation).
- `frontend/src/pages/HomePage.tsx` in the IDE tree is the app's real page, not "Your app starts here".
- `http://<project-id>.preview.localhost` shows the generated product; the browser tab is not "ForgeFlow App".
- `grep -ri "your app starts here\|learn vite" ` over the workspace returns nothing.

Expect prompt-only to be *probabilistic*: if a placeholder survives, that is the known limitation this phase accepts and `phase-55` closes.

## Rollback / cleanup notes

Fully reversible, code-only — no migrations, no volumes, no artifacts. Revert the commit and restore the `_SKELETON_FILES` literal. Already-generated workspaces are unaffected either way (nothing here rewrites existing sandboxes). `PROMPT_VERSION` moving back is harmless; it only re-keys eval rows.

## Design notes

- **Derived, not enumerated.** The original bug is a maintenance bug: a literal that had to be updated by hand every time the skeleton gained a file, and wasn't. Deriving the set from the template directory removes the failure mode rather than fixing this instance of it. The old literal is kept only as a defensive floor for an environment where the template directory is unreadable.
- **Why the tool surface narrows.** `instantiate_skeleton` is safe as a deterministic step-0 action and unsafe as a model choice — re-running it mid-build overwrites `app.ts`/`routes.tsx` and destroys the route registrations the build just made. Removing it from the model's list costs nothing, since step 0 already guarantees the skeleton is present.
- **Prompt-only is a deliberate, recorded trade.** Editing the template would make placeholder removal deterministic, and was offered and declined. The accepted risk is that a model occasionally leaves a placeholder; `phase-55`'s check converts that from an invisible regression into a repairable finding, which makes the template edit a later optimization rather than a prerequisite.
- **Overwrite over delete.** Beyond the `vitest run` exit-1 trap, overwriting keeps every path stable for the IDE, the file tree, and `git diff` — a deleted-then-recreated file reads as churn in the repair loop's diff context.
