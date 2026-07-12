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

⬜ todo
