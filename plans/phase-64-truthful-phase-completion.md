# Phase 64 — Truthful phase completion: reasoning-aware tool loop, evidence-gated phases, structural template-replacement check

## Title & ID

- **ID:** `phase-64`
- **Title:** Truthful phase completion — reasoning-aware tool loop, evidence-gated phases, structural template-replacement check, larger output budgets
- **Epic:** Epic 13 — Build Reliability *(added per user directive)*
- **Milestone:** M2 *(hardens M2's stated outcome — a generated **running app**)*
- **Status:** ✅ done *(2026-09-19)*

## Goal

A build phase can only be recorded `done` when it demonstrably did its work, a model turn that ran out of output budget is never mistaken for "the model finished", and verification proves the template was *replaced by feature code* rather than merely re-worded — so a complex app can no longer ship as an empty "My App / Welcome to the app." shell while the report says 8/8 phases complete.

## Context

**Reported 2026-09-19 (user, with a screenshot):** on *complex* apps (a blog + discussions site with auth, roles and moderation) Build consistently leaves the preview on a placeholder page, and repeated chat requests to "remove the skeleton demo placeholder and put your app there" change nothing. Simple apps (todo, notes) build fine, even on weaker models. The model in use is `z-ai/glm-5.3-flash` via OpenRouter (`LLM_PROVIDER=openai`).

**Root cause — verified at four levels on the live instance** (project `Blog & Discussions Site`, `6aa502ba5573d8d3bf43fc0e`):

1. **Wire.** OpenRouter streams GLM's thinking as `delta.reasoning` / `delta.reasoning_details` chunks. `_accumulate` (`app/agents/anthropic_client.py:512-566`) reads only `delta.content` and `delta.tool_calls`, so reasoning is invisible to the loop — but it is billed as `completion_tokens` and counts against `max_tokens` (8192 on this instance). A probe of a realistic "implement the auth phase" turn returned **8190 of 8192 completion tokens as reasoning, 0 content, 0 tool calls, `finish_reason=length`**. A trivial turn (a `list_dir`) fit in 38 tokens. That is precisely the simple-vs-complex split the user observed: for a todo model the thinking fits beside the tool call; for cookie sessions + bcrypt + password reset it does not, and the call is never emitted.
2. **Loop.** `run_tool_loop` (`anthropic_client.py:212-213`) returns `LoopResult(finished=True)` on **any** turn without tool calls. `TurnComplete.stop_reason` is never consulted — the code comment calls it "informational". A truncated-while-thinking turn therefore reads as "the model is done", with `text=""`.
3. **Phase gate.** `PhaseRunner.run` (`app/agents/build_phases.py:332-333`) marks a phase `done` when `finished ∧ typecheck green`. A phase that wrote nothing leaves the workspace exactly as it was, so the typecheck is trivially green. In the live report: phases 2–8 all `status=done, attempts=1, files=[], summary=''`; workspace commits `bc63222…4d35b10` each touch only `phase-plan/*`; the report says **8/8 complete**; `backend/src/features` and `frontend/src/features` contain only the skeleton READMEs.
4. **Verification.** The placeholder gate (`app/orchestrator/stages/build_verify.py:67`) is a literal-string grep for `Your app starts here` / `Learn Vite` / `ForgeFlow App`. The repair loop — whose context is four small files, so *its* reasoning fit the budget — produced the smallest diff that satisfies the grep (commit `887c09c`): `ForgeFlow App → My App`, HomePage → `Home / Welcome to the app.`. The gate passes; the app is an empty shell; and every later refine ("remove the placeholder") is classified `small`, runs one synthetic phase, hits the same truncation, and reports `done, 0 files`.

Two aggravators, not root causes: the "existing feature code" map is computed once per build (`codegen.py:337`), so phases 2–8 were told *"first build on a bare skeleton"* after phase 1 had written 19 files; and no reasoning is passed back between turns, so the model re-derives its plan from scratch on every tool call (the first build spent 4.3M tokens).

**Decisions (user-selected, 2026-09-19):** *(1)* a truncated or empty turn is **nudged and continued, bounded**, never treated as finished; *(2)* reasoning is **controlled at the provider** through an admin-editable effort/budget knob **and passed back** across turns; *(3)* a phase is `done` only with **≥1 file written + a non-empty closing summary + green typecheck**, with one bounded in-phase retry for a phase that wrote nothing; *(4)* verification adds a **structural** template-replacement check whose findings are **not repairable** — missing code is a build problem, not a patch; *(5)* **output budgets are raised across the board** as a secondary precaution.

**Locked decisions honoured.** The loop stays bounded (D7 / Golden Rule 5): every new behaviour is a *cap* (`llm_max_truncated_turns`, `build_phase_noop_retries`), and the phase gate becomes *stricter*, never looser. Provider-agnosticism (phase-53/57, Golden Rule "route by role, never by vendor") is preserved: the loop keys off `TurnComplete.stop_reason`, which **both** transports already produce, and the reasoning knob travels through the phase-57 repair ladder so an endpoint that rejects it costs one dropped parameter, never a failed call. The skeleton is still a deterministic copy and is not edited (phase-22/54 directive). Cost discipline (Golden Rule 7) improves: the loop stops paying for reasoning it then discards. No new hard prerequisite (D12): a build that cannot prove its phases is reported `partial` and resumable, exactly like a wall-clock stop today.

## Depends on

- **Phase IDs:** `phase-20` (client + loop), `phase-53` (OpenAI transport), `phase-55` (verification), `phase-56` (phased build), `phase-57` (param repair ladder), `phase-51` (layered config), `phase-60` (scope routing — the `small` path reuses the phase runner).
- **External:** none for the unit suite. The manual verification needs the local stack (Colima, `make host-deps`) and an OpenAI-compatible endpoint with a reasoning model (the user's OpenRouter + `z-ai/glm-5.3-flash` reproduces the bug deterministically).

### Prerequisite check

- All of the above are ✅ in `plans/README.md`.
- `cd backend && uv run pytest tests/agents tests/orchestrator tests/config -q` is green before starting (six repair tests need a Docker daemon — pre-existing, environmental).
- `uv run ruff check . && uv run black --check . && uv run mypy .` clean.
- `templates/app-skeleton/` unchanged since phase-58 (the structural check is written against its `app.ts` / `routes.tsx` / `HomePage.tsx`).

## Detailed tasks

### A. Reasoning-aware, truncation-aware tool loop (`app/agents/anthropic_client.py`)

1. **Carry reasoning and truncation through the seam.**
   - `Usage` gains `reasoning_tokens: int = 0` (summed by `plus`). `Cost` (`app/db/models/run.py`) gains `reasoning_tokens: int = 0`, defaulted so existing `Run` documents deserialise unchanged; `record_cost` accrues it. Informational — pricing is unchanged (reasoning is already inside `completion_tokens`).
   - `TurnComplete` gains `reasoning: dict[str, Any] | None = None`: the provider's reasoning payload **as received** — `{"reasoning_details": [...]}` (OpenRouter) and/or `{"reasoning_content": "…"}` / `{"reasoning": "…"}` (Z.ai, DeepSeek, vLLM-style). Opaque to the loop.
   - New stop code `STOP_MAX_TOKENS = "max_tokens"` beside `STOP_MAX_TURNS` / `STOP_NO_PROGRESS`.
2. **`_accumulate` (streamed) and `_from_completion` (non-streamed) collect reasoning.** Read `delta.reasoning` / `delta.reasoning_content` (text, concatenated) and `delta.reasoning_details` (a list of fragments keyed by `index`; reassemble by index, concatenating string fields and keeping the other fields from the first fragment). Read `usage.completion_tokens_details.reasoning_tokens` when present. Emit a new `ReasoningDelta` stream event so `_stream_turn` can publish `agent.reasoning` tokens (typed event, `payload={"text"}`), which stops a two-minute reasoning burst from looking like a hang; the frontend may ignore the event in this phase.
3. **Tolerate a truncated tool call.** When `finish_reason == "length"` and a tool-call fragment's argument JSON does not parse, **drop that call with a warning** and still yield `TurnComplete(tool_uses=[], stop_reason="max_tokens")`. On any other stop reason malformed arguments remain a `ProviderError` (unchanged).
4. **`run_tool_loop`: a turn is finished only when the model says so.** After recording cost:
   ```
   exhausted = turn.stop_reason == "max_tokens"
   empty     = not turn.tool_uses and not turn.text.strip()
   if not turn.tool_uses and (exhausted or empty):
       truncations += 1
       if truncations > max_truncated:            # llm_max_truncated_turns, default 2
           return LoopResult(..., stopped=STOP_MAX_TOKENS)
       append the assistant turn only if it carries text; append the NUDGE as a user turn
       continue
   ```
   The nudge (a module constant, versioned with the prompts): *"Your previous turn ran out of output budget before producing a tool call (or produced nothing). Do not re-derive the plan — reason briefly, then act: call the tool now. Write large files in parts across several `write_file` calls."* A `max_tokens` turn that **does** carry complete tool calls dispatches them normally (the calls are whole; only the trailing text was cut). Each nudged turn is budget-checked and cost-recorded like any other. `LoopResult.finished` keeps its meaning (`stopped is None`) and now cannot be true for a turn the provider cut off.
5. **Pass reasoning back.** `_assistant_message(turn)` attaches `turn.reasoning` under a private top-level key `provider_reasoning` on the assistant message dict (never inside `content`, which stays Anthropic-shaped). `_to_openai_messages` echoes the fields back **only when they were received** — `reasoning_details` verbatim, `reasoning_content` verbatim — so an endpoint that never sent reasoning never receives an unknown field. `SdkTransport` passes messages through a new pure helper `anthropic_messages(messages)` that strips every `provider_reasoning` key (Anthropic rejects unknown message fields). The fake transports in tests see the key and ignore it.
6. **Provider-level reasoning knob.** `build_openai_params` adds `extra_body["reasoning"]` **only when configured**: `llm_reasoning_effort` ∈ `low|medium|high` → `{"effort": …}`; `none` → `{"enabled": false}`; `llm_reasoning_max_tokens > 0` → `{"max_tokens": n}` (both set → both keys). Unset (the default) leaves the body byte-identical to today. This is OpenRouter's unified `reasoning` object; other compatible endpoints either accept it or reject it with a 400 that the phase-57 ladder turns into a `drop` (task 7). The Anthropic transport ignores these keys in this phase (Claude's thinking parameter is a separate, unchanged concern — noted in Design notes).
7. **Repair ladder sees `extra_body`** (`app/agents/provider_compat.py`). `diagnose_param_error` matches a named parameter against top-level params **and** `params["extra_body"]`; `apply_repairs`'s `drop` pops from both. Memo/persistence unchanged (the signature is still `(drop, reasoning)`).

### B. Evidence-gated phases (`app/agents/build_phases.py`, `app/agents/codegen.py`)

8. **A phase must write to be done.** After the implement loop, compute `wrote = set(run.progress.files) - seen_before`. If `wrote` is empty and the loop `finished`: retry **once** (`build_phase_noop_retries`, default 1, admin-editable) by appending an explicit feedback turn — *"This phase produced no files. It owns: `<files>`. Goal: `<goal>`. Write them now with `write_file`, one file per call, the smallest complete version first."* — and re-running the same bounded loop on the accumulated conversation. Count it in `attempts`. If still empty: `status=failed`, `note="wrote nothing after N attempt(s)"`. The typecheck gate runs only when something was written (there is nothing to check otherwise, and running it would only manufacture a green signal).
9. **A phase must say what it did.** If the loop finished with writes but an empty closing text: one nudge turn — *"Reply with a one-line summary of what you wrote in this phase."* If still empty and `build_phase_require_summary` (default `true`) is on: `status=failed`, `note="no closing summary"`. With the flag off, the summary is synthesised (`"wrote N file(s): …"`) and the phase can be `done`.
10. **`done` ⟺ green ∧ finished ∧ wrote ∧ summary.** A loop stopped by `STOP_MAX_TOKENS` is reported like `STOP_MAX_TURNS`: `note="stopped after N truncated turns (output budget)"`. Failed phases stay **carried, not fatal** (phase-56), so the build still reaches verification and the report — and `_resume_point` picks them up next Build, as today.
11. **Refresh the repository map per phase.** In `CodegenAgent._run_phases`, recompute `existing` via `_existing_code(ctx)` before every phase after the first, so phase *N* is told what phases 1…N-1 wrote instead of "first build on a bare skeleton". The inventory is paths + sizes only (unchanged cost posture).
12. **Report + UI truth.** `PhaseOutcome` already carries `files`, `note`, `summary`; `BuildPanel.tsx` phase rows additionally render the file count and the note (`"0 files · wrote nothing after 2 attempts"`), so the failure the user could not see in the screenshot is on screen.

### C. Structural template-replacement check (`app/orchestrator/stages/build_verify.py`, `app/orchestrator/stages/build.py`)

13. **New step 4c — `feature_code`**, after the placeholder check and the demo-test check, before repair. Pure, model-free functions over file contents (unit-tested against the real skeleton):
    - `has_feature_files(tree)`: `backend/src/features/**` holds ≥1 non-test `.ts` file, and `frontend/src/features/**` ≥1 non-test `.ts`/`.tsx` file (README.md never counts). Each half is required only when expected (task 14).
    - `app_mounts_feature_router(app_ts)`: `backend/src/app.ts` imports from `./features/` (or `./routes/` beyond `health`) **and** has an `app.use(` beyond the skeleton's `/health`.
    - `routes_render_feature_code(routes_tsx, index_page_src)`: the index child of `frontend/src/routes.tsx` is not the skeleton's `./pages/HomePage`, **or** that page imports from `features/`.
    Findings are one synthetic result each, reported verbatim; the existing string gate is kept as is (not extended — the words are not the problem).
14. **Expectations come from the plan.** `BuildStageHandler` derives `expect_backend = any(kind ∈ {data, backend})` and `expect_frontend = any(kind ∈ {frontend, wiring})` from `report.phases` (the fallback plan always has both; the `small` path expects both, since it presupposes a previous build). Passed to `BuildVerificationController.run(..., expect=…)`.
15. **Not repairable.** A `feature_code` finding short-circuits exactly like `demo_test_left`: `STOP_NO_FEATURE_CODE = "no_feature_code"`, **zero repair iterations, zero tokens**, message *"The build wrote no feature code — the app is still the template."*, hint *"N phase(s) wrote nothing; run Build again to resume them."* (`N` from the report). `BuildVerifyReport` gains `feature_code_ok: bool`; `_gate_stop_reason` orders it after `placeholder`. `build.py::_summarize` and the frontend `STOP_REASON_LABELS` gain the new vocabulary entry. The placeholder finding itself still goes to repair (a *worded* leftover on a real app is a legitimate small patch); the structural finding never does.

### D. Output budgets (secondary precaution, user-directed)

16. **Defaults (`app/core/config.py`, `.env.example`, `config_registry.py` descriptions):** `anthropic_max_output_tokens` 4096 → **16384**; `tool_output_max_chars` 8000 → **16000** (a truncated typecheck tail hid errors); `build_phases._trim` limit 4000 → **8000**; `_STORY_MAX_CHARS` 400 → **800**. Registry maxima already allow these.
17. **This instance.** A stored admin override (8192) wins over a code default (phase-51 precedence), so the executor raises `anthropic_max_output_tokens` to 16384 **through the admin config service** (an audited `config_audit` write, not a silent DB edit), and records it in the phase's implementation notes.

### E. Config, registry, docs

18. New `Settings` fields, all admin-editable under `Cat.models` / `Cat.build` with descriptions, min/max, and `.env.example` entries: `llm_max_truncated_turns: int = 2` (1–10), `llm_reasoning_effort: str = ""` (`""|none|low|medium|high`, validated), `llm_reasoning_max_tokens: int = 0` (0 = unset), `build_phase_noop_retries: int = 1` (0–3), `build_phase_require_summary: bool = True`. `tests/config/test_registry_complete.py` must stay green.
19. Bump `PROMPT_VERSION` (`app/agents/prompts/__init__.py`) — the nudge and feedback texts are prompt surface.
20. `plans/README.md`: status row, changelog row, "Additions beyond the roadmap" item 9 (this file's Context, condensed).

## Out of scope for this phase

- Rendering the `agent.reasoning` stream in the Build conversation UI (the event is emitted; a collapsible "thinking" trace is a UI follow-up).
- Mapping `llm_reasoning_*` onto Anthropic's `thinking` parameter for `SdkTransport` — Claude thinking is separately budgeted and not implicated; a later phase can add it behind the same keys.
- Editing `templates/app-skeleton/` (phase-54 directive: prompt-only + deterministic checks).
- Re-planning or re-running failed phases *automatically inside the same Build* beyond the one in-phase retry — the next Build resumes them (phase-56 resume), keeping the wall-clock bound meaningful.
- The 4.3M-token context growth from re-reading files inside one phase (a separate context-budget concern; reasoning pass-back addresses the largest part of it).

## Acceptance criteria

- A scripted transport that answers with `TurnComplete(text="", tool_uses=[], stop_reason="max_tokens")` and then a real `write_file` makes `run_tool_loop` **nudge once and continue**; the transcript contains the nudge; `finished` is `True` only after the model's genuine end turn. `llm_max_truncated_turns + 1` such turns end the loop with `stopped == STOP_MAX_TOKENS`, `finished == False`. An empty `end_turn` turn is handled identically. A `max_tokens` turn carrying complete tool calls dispatches them.
- `_accumulate` on scripted OpenRouter-style chunks yields `TurnComplete.reasoning` with `reasoning_details` reassembled by index and `usage.reasoning_tokens` set; a `length` stream with a half-built tool-call JSON yields `stop_reason="max_tokens"` and no tool uses **without raising**.
- `_to_openai_messages` echoes `reasoning_details` / `reasoning_content` back on the assistant turn **only** when they were received; `anthropic_messages()` strips `provider_reasoning`; `build_openai_params` is byte-identical to today when no reasoning setting is configured and adds `extra_body.reasoning` in the documented shape when one is.
- A provider 400 naming `reasoning` diagnoses to `Repair(drop, "reasoning")` and `apply_repairs` removes it from `extra_body`.
- A phase whose loop writes nothing is retried exactly once with the feedback turn, then recorded `failed` with `note` starting `wrote nothing`; the build report is `partial` with `stop_reason == "phases_incomplete"` (or `no_feature_code` from verification), and `_resume_point` on the next build starts at that phase. A phase that writes files but returns no closing text is nudged once; with `build_phase_require_summary` on it fails, off it is synthesised and `done`.
- The phase-2 prompt of a scripted build lists the files phase 1 wrote (per-phase inventory refresh).
- Against a workspace that is the skeleton plus `My App` / `Home / Welcome to the app.` and no `features/` files, verification returns `stop_reason == "no_feature_code"`, `feature_code_ok == False`, and the repair factory is **never invoked**. Against a workspace with a feature router mounted, a feature page rendered from the index route and feature files on both sides, `feature_code_ok == True` and the gate is unchanged from today.
- Defaults: `anthropic_max_output_tokens == 16384`, `tool_output_max_chars == 16000`; registry complete; `.env.example` updated.
- `ruff` / `black` / `mypy` clean; the existing agent, orchestrator, config and frontend suites green; the Build panel shows per-phase file counts and notes.

## Tests to write

**Control plane (`pytest`)**

- `tests/agents/test_tool_loop_truncation.py` *(new)* — nudge-and-continue on `max_tokens`; nudge on empty `end_turn`; cap → `STOP_MAX_TOKENS`; complete tool calls on a `max_tokens` turn still dispatch; cost recorded for nudged turns; budget enforced on nudged turns.
- `tests/agents/test_openai_transport.py` *(extend)* — reasoning deltas and `reasoning_details` reassembly; `reasoning_tokens` from usage details; truncated tool-call JSON on `length` is dropped, not raised; echo-back only-when-received; `anthropic_messages()` strip; `build_openai_params` reasoning shapes (`effort`, `none`, `max_tokens`, unset ⇒ identical).
- `tests/agents/test_provider_param_extra_body.py` *(new)* — diagnose + drop of an `extra_body` key; memo signature unchanged.
- `tests/agents/test_codegen_phase_evidence.py` *(new)* — wrote-nothing retry then `failed`; summary nudge with the flag on/off; `done` requires all four conditions; per-phase `existing` refresh; report `partial` + resume point.
- `tests/orchestrator/test_build_verify_feature_code.py` *(new)* — the three pure checks against the **real** skeleton files (skeleton ⇒ findings; a minimal generated app ⇒ none); controller short-circuits with `no_feature_code` and zero repair calls; `expect_backend=False` skips the backend half.
- `tests/config/test_registry_complete.py` *(existing)* — green with the five new keys.
- Adjust if they pin old defaults: `tests/agents/test_tool_loop_bounds.py`, `test_codegen_phased.py`, `test_codegen_unfinished.py`, `tests/orchestrator/test_build_verify*.py`.

**Frontend (Vitest + Testing Library)**

- `frontend/src/features/build/BuildPanel.test.tsx` *(extend)* — `no_feature_code` renders its label; a phase row renders `0 files · wrote nothing after 2 attempts`.

## Manual verification steps

```bash
cd backend && uv run pytest tests/agents tests/orchestrator tests/config -q
uv run ruff check . && uv run black --check . && uv run mypy .
cd ../frontend && pnpm test && pnpm lint
```

Then, with the stack up (`make host-deps`, API + web running), in **/admin → Models**: confirm `anthropic_max_output_tokens = 16384`, set `llm_reasoning_effort = low` (GLM/OpenRouter). Open **Blog & Discussions Site** and press **Rebuild** (regenerate everything):

- Every phase row shows a non-zero file count; no row says *wrote nothing*. If one does, the report is `partial`, the stop reason names it, and pressing **Build** again resumes exactly that phase.
- `docker run --rm -v forgeflow-vol-<project-id>:/workspace:ro alpine sh -c 'find /workspace/backend/src/features /workspace/frontend/src/features -type f | grep -v README; grep -n "features/" /workspace/backend/src/app.ts /workspace/frontend/src/routes.tsx /workspace/frontend/src/pages/HomePage.tsx'` — feature files exist on both sides and both entry points reference them.
- `http://<project-id>.preview.localhost` shows the blog, not `Home / Welcome to the app.`; the tab title is the app's name.
- The Build conversation's cost line shows reasoning tokens; a build no longer spends minutes in silence (the `agent.reasoning` events keep `agent.stream.*` alive).
- Negative check: temporarily set `llm_reasoning_effort` back to unset and `anthropic_max_output_tokens` to 2048, press Build on a new complex project — phases must now come back **failed: stopped after 2 truncated turns** / **wrote nothing**, the report `partial`, never `done` with 0 files.

## Rollback / cleanup notes

Code-only; no migrations, no volume changes. `Cost.reasoning_tokens` and `BuildVerifyReport.feature_code_ok` are defaulted additive fields — old documents and reports read unchanged; reverting the code leaves the extra fields inert. The five new settings default to off/unset; deleting their rows from `platform_settings` (or leaving them) is harmless. The stored `anthropic_max_output_tokens` bump is reverted in **/admin**. `PROMPT_VERSION` moving back only re-keys eval rows. Workspaces already built are untouched either way — a project stuck on the shell is fixed by pressing **Rebuild** after this phase, not by the phase itself.

## Design notes

- **Nudge, don't raise, and don't trust silence.** A `max_tokens` turn is not a provider fault (the request succeeded) and not the model finishing (it was cut off). Raising would discard the build report; returning `finished` is the bug. A bounded corrective turn is the only option that is both honest and cheap, and it works for both transports because both already report the stop reason — the fix is above the vendor seam, per phase-53's contract.
- **Echo what you received.** Passing reasoning back is how OpenRouter (and DeepSeek/Z.ai directly) preserve chain-of-thought across tool calls, and it is the largest lever on the 4.3M-token burn. Echoing *only* fields the endpoint itself produced keeps every other provider's request byte-identical, so no provider is privileged and no new quirk is introduced for OpenAI proper.
- **The knob is a hint, the loop is the guarantee.** `llm_reasoning_effort=low` will likely prevent the truncation on GLM outright; it is still only a request the provider may ignore. Tasks A4 and B8 are what make the build *truthful* regardless of what any model does.
- **Why the structural finding is not repairable.** The repair loop is diff-aware and minimal by design (D7). Handed "the app is still the template", minimality *is* the failure mode: the smallest diff that satisfies the check is a re-wording. Missing feature code is not a patch; it is the phase loop's job, and the resume path already exists.
- **Why "wrote nothing" gets one retry, not more.** The feedback turn carries the one thing the first attempt lacked — an explicit statement that the output must be files — and the phase-57/A6 controls address the underlying cause. A second identical failure is evidence the model cannot do this phase in this budget; more attempts are spend without new information (the same reasoning as the repair loop's stall threshold).
- **Summary requirement.** Required per the user's choice: an empty closing text was one of the two signals (with `files=[]`) that would have exposed this failure on day one. `build_phase_require_summary` exists so an operator running a terse model can trade that signal for fewer re-runs without a deploy.
- **Budgets.** 16384 output tokens is the smallest value that comfortably holds a reasoning burst *plus* a 300-line file on current reasoning models; it changes nothing for models that stop early, since output is billed as used, not as capped.

## Implementation notes (as-built, 2026-09-19)

- **The summary rule collapsed into the loop.** Task B9 planned a phase-level "reply with a one-line summary" nudge. As built, the *client's* truncation handling (A4) already nudges an **empty** turn — not only a `max_tokens` one — so a loop that `finished` always carries the model's last words; a phase-level summary nudge would have been unreachable. `build_phase_require_summary` therefore governs the one case that remains: a model that wrote its files and then produced nothing usable through every nudge, which the loop reports as `stopped=max_tokens`. Required (default) ⇒ that phase fails with `no closing summary — the model produced nothing after its last write`; optional ⇒ the line is synthesised from the files and the phase can be `done`. The `done` condition is still `green ∧ finished ∧ wrote ∧ summary`, as specified.
- **Reasoning events are batched.** A real think arrives as thousands of one-word deltas (8,190 chunks in the probe). `agent.reasoning` is emitted per ~400 characters, flushed before any answer token and at the end of the turn, so a think stays visible without evicting the whole realtime ring.
- **Comments never count.** The skeleton's own `app.ts` carries `//   app.use('/api/todos', todosRouter)` as an example; the structural check strips `//` and `/* */` comments before looking for a mounted router or a `features/` import, otherwise the untouched template passed the "router mounted" test.
- **Ordering of the short-circuits.** The feature-code check runs as step 4b, *before* the demo-test guard (now 4c): "nothing was built" is the more fundamental verdict, and telling a user to rewrite the demo tests of an app that does not exist would be the wrong instruction.
- **Test harness adjustments.** Scripted phases in `test_codegen_phased.py` / `test_codegen_resume.py` that expected a phase to be `done` without writing anything now write one file (`codegen_fakes.phase_write`) — that is the rule this phase introduces, not a loosening of the tests. The verifier tests inject a no-findings `feature_code` step where the check is not their subject; the handler's `FakeVerifier` accepts the new `expect` / `phases_wrote_nothing` kwargs.
- **This instance.** The stored admin override `anthropic_max_output_tokens=8192` was raised to `16384` through `ConfigAdminService.set` (validated, `config_audit`-recorded) — a stored override wins over the new code default. `llm_reasoning_effort` was left unset; setting it to `low` for `z-ai/glm-5.3-flash` is the recommended next step (Manual verification).
- **Pre-existing.** `mypy` reports four `Any | None` index errors in `tests/design/` on the pre-phase tree; `app/` is clean. Not touched here.
