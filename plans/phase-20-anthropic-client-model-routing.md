# Phase 20 — Anthropic client & model routing

## Title & ID

- **ID:** `phase-20`
- **Title:** Anthropic client & model routing
- **Epic:** Epic 4 — Build Stage (agentic codegen)
- **Milestone:** M2

## Goal

A robust Claude client (Messages API, tool use, streaming) with model routing (Sonnet for codegen, Haiku for routing/classify/summaries), retries/backoff, per-call token/₹ accounting, and budget guardrails.

## Context

The engine behind every agent (D4). Cost discipline (§7, D4, risk §12) is enforced here: `MODEL_CODEGEN` vs `MODEL_ROUTING`, per-project + global budget caps that halt runs with a clear message. All model IDs/keys are config-resolved (`admin > env > default`) so the admin dashboard can change them (`phase-51/52`).

## Depends on

- **Phase IDs:** `phase-07`
- **External:** `ANTHROPIC_API_KEY`; `MODEL_CODEGEN`, `MODEL_ROUTING`, budget caps in config.

### Prerequisite check

- `phase-07` ✅ (Run/token capture, artifacts); `ANTHROPIC_API_KEY` present.

## Detailed tasks

1. **Client (`app/agents/anthropic_client.py`).** Wrap the `anthropic` SDK: async Messages calls, **streaming** (token deltas → `agent.token` events), **tool-use loop** (accepts tool schemas, dispatches tool calls, feeds results back), system-prompt support, stop conditions, max-tokens.
2. **Model router (`app/agents/models.py`).** `route(task_kind) -> model_id`: `codegen|testgen|repair → MODEL_CODEGEN`; `classify|summarize|route|title → MODEL_ROUTING`. IDs from config resolver; a single place to change them.
3. **Retries/backoff.** Exponential backoff on 429/5xx/transient network; jitter; caps; classify failures as `ProviderError`.
4. **Cost accounting.** Every call records `{model, input_tokens, output_tokens, inr}` onto the active `Run` (`phase-07`) and against the project; a pricing map (config) converts tokens→₹.
5. **Budget guardrails.** Before/after each call, check per-project + global caps (`BUDGET_CAP_INR_PER_PROJECT`, `BUDGET_CAP_INR_GLOBAL`); on breach, **halt** the run with a clear `UserError` and emit a `budget.halt` event. Caps are config-resolved.
6. **Prompt scaffolding (`app/agents/prompts/`).** Base system prompts + helpers; kept versioned for eval reproducibility.

## Out of scope for this phase

- The specific agent tools (`phase-21`) and the codegen/testgen/repair agents (their phases). This is the **client + router + cost/budget** substrate, tested against a mock.

## Acceptance criteria

- A streamed tool-use loop completes against a **mocked** Anthropic transport (tokens streamed, tool calls dispatched, results fed back).
- Router returns Sonnet for code tasks, Haiku for routing/classification tasks (config-driven).
- Token usage + ₹ recorded per call on the `Run`/project.
- Exceeding a budget cap **halts** with a clear message + `budget.halt` event; nothing runs past the cap.

## Tests to write

- `backend/tests/agents/test_client_tool_loop.py` — mocked streaming + multi-turn tool use.
- `backend/tests/agents/test_model_router.py` — task→model mapping honors config.
- `backend/tests/agents/test_cost_accounting.py` — tokens/₹ recorded correctly.
- `backend/tests/agents/test_budget_cap.py` — per-project + global caps halt runs; event emitted.

## Manual verification steps

1. Run a scripted mock tool-use loop → tokens stream to a client; tool results fed back; final message returned.
2. Set a tiny per-project cap → run → halts with a clear message; cost dashboard (later) shows the spend.

## Rollback / cleanup notes

- No persistent external state beyond `Run` records (removable). Reversible.

## Design notes

- **Pricing map is config** (per-model ₹/token) so rates update without code — and are admin-editable at `phase-51/52`.
- The tool-use loop is provider-tool-agnostic; `phase-21` supplies the concrete sandbox tools.

## Status

✅ done — Opens Epic 4. The agent substrate: a Claude client with a streamed, bounded tool-use loop,
model routing, and cost/budget guardrails, all tested against a mocked transport.

- `app/agents/models.py` — `route(TaskKind) -> model_id`: codegen/testgen/repair → `MODEL_CODEGEN`,
  classify/summarize/route/title → `MODEL_ROUTING` (config-resolved). `TaskKind` is a plain `Enum`
  (not `StrEnum`) so a member like `title` can't shadow a `str` method.
- `app/agents/anthropic_client.py` — `AnthropicClient.run_tool_loop`: per turn it enforces budget →
  streams the turn (text deltas → `agent.token` events) → records cost on the `Run` → enforces
  budget again → dispatches any tool calls and threads results back, else returns. Bounded by
  `anthropic_max_tool_turns`. The transport is a `Protocol` (fully mocked in tests); the real
  `SdkTransport` lazy-imports `anthropic` (only needed for live calls) and adds 429/5xx/network
  backoff — classified as `ProviderError` (`pragma: no cover`, no SDK/creds in tests).
- `app/agents/cost.py` + `pricing.py` — `record_cost` accrues `{tokens, ₹}` onto the `Run`;
  `project_spend_inr`/`global_spend_inr` roll up via a Mongo `$sum`. Pricing is **config**
  (`MODEL_PRICING_JSON`, ₹/Mtok) with built-in defaults; an unpriced model costs ₹0.
- `app/agents/budget.py` — `enforce_budget` raises a clear `UserError` **and** emits `budget.halt`
  when the per-project or global cap is reached; nothing runs past the cap (checked before *and*
  after each call).
- `app/agents/prompts/` — versioned base system prompt (`PROMPT_VERSION`) for eval reproducibility.
- Config/.env: `ANTHROPIC_MAX_OUTPUT_TOKENS`/`MAX_TOOL_TURNS`/`MAX_RETRIES`, `MODEL_PRICING_JSON`;
  new `budget.halt` event type.

**Acceptance:** streamed multi-turn tool loop against a mocked transport (tokens streamed, tool
calls dispatched, results fed back); router honors config; tokens/₹ recorded per call on the Run +
project/global rollups; a breached cap halts (before *and* after a call) with a clear message +
`budget.halt` event. All covered by `tests/agents/` (15).

**Verification:** ruff/black + mypy (phase-20 surface) clean; agents suite **15 passed**; full
non-sandbox backend suite **236 passed** (no regressions). `anthropic` isn't installed in the test
venv — by design the SDK path is lazy-imported and the loop is tested via the fake transport. Same
Windows-env notes as prior phases (isolated FastAPI 0.139.0 venv; `tests/sandbox` untouched/skipped).

## Follow-up fix — the turn bound discarded finished work

A real build failed with:

```
app.core.errors.UserError: Agent tool loop exceeded 16 turns without finishing
```

Two problems, one symptom.

**The bound was raising, not reporting.** Every file the model had written was already on disk and
committed, but the exception propagated out of `CodegenAgent.run` → the Build stage failed and the
user saw no report, no summary, and no hint that the work was resumable. `run_tool_loop` now returns
a `LoopResult` carrying `stopped` (`max_turns` / `no_progress`, `None` when the model finished on its
own) and the callers present it: an unfinished build reports its files with a leading follow-up
("The build did not finish — it stopped after N tool turns (its safety bound). Run Build again to
continue from here."), and testgen's report says the same about partially written suites. The loop is
just as bounded as before (D7) — the difference is that a bound is now an *outcome*, not a crash.

**No-progress detection, applied to this loop too.** With the bound raised to hundreds of turns, a
model that repeats one identical call would burn every remaining turn (and its tokens) before
stopping. `REPEAT_LIMIT` identical consecutive calls — same tool names, same arguments — now ends the
loop with `STOP_NO_PROGRESS`. Varied work is unaffected: a different path per turn, or the same tool
with different arguments, runs to the bound (asserted).

**Config note:** the code default has been 250 for exactly this reason ("one turn per tool call, so a
full-stack build needs hundreds"), and `.env.example` says 250 — but this machine's `.env` still held
the old `16`, which is what produced the failure. That value governs; the DB/admin layer had only
`llm_provider`, the two model ids, and the OpenAI-compatible key/base URL.

Verified: 8 new tests (`tests/agents/test_tool_loop_bounds.py`, `tests/agents/test_codegen_unfinished.py`).
