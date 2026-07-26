# Phase 53 — OpenAI-compatible LLM provider

## Title & ID

- **ID:** `phase-53`
- **Title:** OpenAI-compatible LLM provider
- **Epic:** Epic 12 — Admin & Configurability *(added per user directive)*
- **Milestone:** M5
- **Status:** ✅ done

## Goal

Let ForgeFlow's agents run against **any OpenAI-compatible Chat Completions endpoint** — OpenAI, Azure OpenAI, Together, Groq, OpenRouter, vLLM, Ollama, and similar — as an alternative to the default Anthropic Messages API, selectable at runtime with a single config key and **without changing any agent, tool, orchestration, or cost code**.

## Context

The **user directive**: *"this project currently is dependent entirely on anthropic apis and base urls and models; please make it also able to run with openai compatible apis and base urls and models."*

This extends a locked decision (§1 of the plan pins the `anthropic` SDK for the control plane's agents), so it is recorded here rather than applied silently. It stays faithful to every *other* locked decision: the generated-app stack is untouched, the repair loop stays bounded, cost discipline and secret handling are preserved, and Anthropic remains the default — nothing changes for existing installs.

The change is small because the phase-20 agent client already isolated the network behind a `Transport` Protocol. The entire Anthropic-specific surface lived in one class (`SdkTransport`); the loop, tools, model routing, budget guard and cost accounting were already provider-agnostic. Adding a provider therefore means adding one more `Transport` — a pure adapter — plus config to choose it.

## Depends on

- **Phase IDs:** `phase-20` (agent client + `Transport` seam, cost/budget), `phase-01` (layered config resolver), `phase-51/52` (admin config surface — the new keys are admin-editable for free).
- **External:** `openai` Python SDK (**optional** dependency; only needed when the provider is selected).

### Prerequisite check

- `phase-20` ✅ (`Transport` Protocol, `run_tool_loop`, `Usage`/`record_cost`, `enforce_budget`), `phase-01` ✅ (config), `phase-51/52` ✅ (admin config catalog reads the registry, so `llm_provider`/`openai_*` appear automatically).

## Detailed tasks

1. **`OpenAITransport` (`app/agents/anthropic_client.py`).** A second `Transport` implementation over `openai.AsyncOpenAI(...).chat.completions.create(stream=True)`. Lazy-imports the SDK (like `SdkTransport`), retries transient errors (`APIConnectionError`/`RateLimitError`/`InternalServerError`) with the same bounded backoff, and requests `stream_options={"include_usage": True}` so token usage still lands on the `Run`.
2. **Pure translation helpers.** `_to_openai_messages` / `_to_openai_tools` map the loop's **Anthropic-native** request (system prompt, content blocks, `tool_use`, `tool_result`, `input_schema`) into Chat Completions shape (`role="system"`, `tool_calls`, `role="tool"` messages, `function.parameters`). `_accumulate` folds the streamed response back into the loop's `TextDelta`/`TurnComplete` events, reassembling tool calls that arrive **fragmented and keyed by index** (id/name once, argument JSON across chunks) and reading usage off the trailing chunk. These are module-level and unit-tested.
3. **Provider selection (`default_transport()`).** Reads `LLM_PROVIDER` (`anthropic` default | `openai`); unknown values fail loudly with a `SystemError`. `AgentClient` (the historical `AnthropicClient`, now aliased) calls it when no transport is injected, so all existing call sites pick up the configured provider unchanged.
4. **Config (`app/core/config.py`, `.env.example`).** New keys: `llm_provider`, `openai_api_key`, `openai_base_url` (blank → SDK default; set to any compatible base URL), `openai_max_retries`. Model ids reuse the existing `MODEL_CODEGEN`/`MODEL_ROUTING` (the single source `route()` already reads) — set them to the target provider's ids.
5. **Pricing (`app/agents/pricing.py`).** Placeholder ₹/Mtok rates for `gpt-4o`/`gpt-4o-mini`; any other id prices at ₹0 until `MODEL_PRICING_JSON` supplies a rate — the existing operator-priced path, so budgets remain enforceable.
6. **Optional dependency (`pyproject.toml`).** `openai>=1.40` under `[project.optional-dependencies].openai`; the default (Anthropic) install is unchanged. Install with `uv sync --extra openai`. A missing SDK raises a clear `ProviderError` pointing at that command.
7. **Tests (`tests/agents/test_openai_transport.py`).** Cover request translation (incl. assistant-with-only-tool-calls → `content: null`), stream reassembly (fragmented multi-chunk tool call + usage + stop-reason mapping), malformed-argument rejection, and provider selection (anthropic/openai/unknown).

## Design notes

- **Anthropic stays the internal vocabulary; OpenAI is an edge adapter.** The loop keeps speaking content blocks / `tool_use` / `tool_result`; only the transport translates. This is why zero other modules changed and why the default install and behaviour are byte-for-byte unchanged. Translating the whole (stateless) transcript on each turn keeps the adapter simple and correct — no per-conversation adapter state.
- **Fail loud, not silent.** An unknown `LLM_PROVIDER` and a missing `openai` package both raise typed errors; malformed tool-call arguments raise `ProviderError` rather than being coerced to `{}` (a wrong tool call must not look like an empty one).
- **Honest cost.** Usage is read from the stream when the endpoint reports it; an endpoint that omits usage yields ₹0 (the same "must be priced to count" contract as any unlisted model), never a fabricated number.
- **Already admin-configurable.** Because the new keys are ordinary `Settings` fields, the phase-51/52 admin dashboard surfaces `llm_provider` / `openai_base_url` / models with no extra work.

  *Follow-up:* the phase-51 registry originally listed only 18 curated keys, so in practice `llm_provider` and `openai_*` were **not** reachable from the panel. That was corrected — the registry now covers every `Settings` field, and `OPENAI_API_KEY` is settable there as a **write-only, encrypted** secret rather than being refused. Switching provider end-to-end (`LLM_PROVIDER` → `openai`, base URL, both model ids, the key) is now a single **Models & provider** section in `/admin`, live on the next agent call. A regression test (`tests/config/test_registry_complete.py::test_the_phase_53_provider_keys_are_admin_editable`) pins it.

## Out of scope for this phase

- Anthropic-specific features with no Chat Completions analogue (extended thinking, prompt caching, server tools) when running under OpenAI — the adapter covers text + function tool-calling, which is what the ForgeFlow loop uses.
- Multimodal image inputs over the OpenAI provider (the codegen loop passes design as text). 
- Changing the **generated-app** stack (locked) — this phase concerns only the control-plane agents.
- Auto-translating model ids between providers (the operator sets `MODEL_CODEGEN`/`MODEL_ROUTING`).

## Acceptance criteria

- ✅ `LLM_PROVIDER=anthropic` (default) behaves exactly as before; the default install needs no new packages.
- ✅ `LLM_PROVIDER=openai` routes every agent call (codegen/testgen/repair/routing) through `OpenAITransport` with no change to those modules.
- ✅ `OPENAI_BASE_URL` retargets to any compatible endpoint; `MODEL_CODEGEN`/`MODEL_ROUTING` choose the models.
- ✅ Streaming (token events), bounded tool-use loop, per-`Run` cost, and budget enforcement all work under the OpenAI provider.
- ✅ Unknown provider and missing SDK fail with clear typed errors.
- ✅ Unit tests green; `ruff`/`black`/`mypy` clean; no regression in the existing agent suite.

## What shipped

- **Backend:** `OpenAITransport` + translation/reassembly helpers + `default_transport()` in `app/agents/anthropic_client.py` (module renamed in spirit to the provider-agnostic *agent client*; `AgentClient = AnthropicClient` alias kept to avoid churn); config keys in `app/core/config.py`; placeholder pricing in `app/agents/pricing.py`; optional `openai` extra in `pyproject.toml`; `.env.example` "LLM provider" section.
- **Tests:** `tests/agents/test_openai_transport.py` — **9 passed**; full agents suite unaffected; ruff/black/mypy clean.
- **Docs:** this file; `plans/README.md` row 53; `DEVELOPER.md` provider note.

## Follow-up — Mistral (and any strict-schema endpoint) would have failed on every call

Asked whether Mistral AI can be used as the provider. It can, over this phase's `LLM_PROVIDER=openai`
path — but one thing had to be fixed first.

The transport always sent `stream_options: {"include_usage": true}` (the token-usage trailer, so cost
accounting has real numbers). Mistral's chat-completions schema has no such field and validates
strictly: it answers **`422 extra_forbidden` — "Extra inputs are not permitted"**. Every request would
have failed, before any model even ran. Azure's serverless deployments behave the same way. (Mistral's
own docs say `stream_options.include_usage` *must* be set to get usage while streaming, which
contradicts the 422s users report — so relying on either reading would be a coin flip.)

The transport now treats that specific refusal as a **repairable request, not an outage**: it drops
the trailer, remembers that this endpoint refuses it (`_NO_STREAM_USAGE`, keyed by base URL, so the
extra round trip happens once per process), and retries. Everything else in the body — `model`,
`messages`, `max_tokens`, `stream`, `tools` — is a documented Mistral field, asserted by a test.
Request building moved into a pure `build_openai_params(request, *, stream_usage)` so the wire format
is testable without a network.

Consequence to know: on such an endpoint no usage arrives, so token counts (and therefore ₹ cost)
read **0** for it. The budget caps in phase-20 are cost-based, so they cannot bind there — the
tool-loop turn bound is what limits spend on these providers.

Classification is deliberately narrow: the error must name `stream_options`/`include_usage` **and**
either carry a strictness marker or a 4xx status. A 500 that merely mentions the field stays with the
transient retries, and a rate-limit or a tool-support rejection is never mistaken for it — otherwise
usage accounting would be silently disabled for a perfectly healthy provider.

Verified: 12 new tests (`tests/agents/test_openai_strict_endpoints.py`); `.env.example` documents the
Mistral endpoint, a tool-calling model, and the zero-usage caveat.
