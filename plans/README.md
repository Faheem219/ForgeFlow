# ForgeFlow — Phase Plans Index

Per-phase execution specs generated from **Section 10** of [`IMPLEMENTATION_PLAN.md`](../IMPLEMENTATION_PLAN.md). Each `phase-NN-*.md` is a self-contained, independently-shippable slice: goal, context, dependencies + prerequisite check, file-level tasks, out-of-scope, testable acceptance criteria, tests, manual verification, rollback notes, and status.

## How to use these plans

1. **Never start blind.** Before executing a phase, re-read the locked decisions (D1–D14) in `IMPLEMENTATION_PLAN.md` §1 and `CLAUDE.md` (the operating manual). The plan is *what* to build; `CLAUDE.md` is *how* to work.
2. **Execute on request only.** When the user says **"execute phase N"**: open `plans/phase-NN-*.md` + its `Depends on`; verify prerequisites are ✅; implement end-to-end (code + tests); run lint/type/tests; ensure the app still boots; meet **every** acceptance criterion; update this table's **Status** and the phase file's **Status**; commit with a Conventional Commit referencing the phase. Then **stop** — never start the next phase unprompted, never expand scope.
3. **If a phase would force deviating** from the locked decisions, **pause and ask** (Golden Rule 1).
4. **Status legend:** ⬜ todo · 🔄 in progress · ✅ done. Keep this table and each file's Status in sync.
5. **Template:** new/adjusted phases follow [`PHASE_TEMPLATE.md`](./PHASE_TEMPLATE.md).

## Status table

| ID | Title | Epic | Depends on | Status |
|----|-------|------|-----------|--------|
| [01](./phase-01-repo-tooling.md) | Repo, tooling & local dev | 0 · Foundations | — | ✅ |
| [02](./phase-02-data-layer.md) | Control-plane data layer | 0 · Foundations | 01 | ✅ |
| [03](./phase-03-auth.md) | Auth & user management | 0 · Foundations | 02 | ✅ |
| [04](./phase-04-frontend-shell-pwa.md) | Frontend app shell & PWA base | 0 · Foundations | 01 | ✅ |
| [05](./phase-05-realtime-hub.md) | Realtime event hub | 0 · Foundations | 03 | ✅ |
| [06](./phase-06-project-lifecycle-state-machine.md) | Project lifecycle & stage state machine | 1 · Orchestration Core | 02, 03 | ✅ |
| [07](./phase-07-conversation-artifact-model.md) | Conversation & artifact model | 1 · Orchestration Core | 06 | ✅ |
| [08](./phase-08-conductor.md) | Conductor (orchestrator) | 1 · Orchestration Core | 05, 06, 07 | ✅ |
| [09](./phase-09-workspace-ui-shell.md) | Workspace UI shell | 1 · Orchestration Core | 04, 05, 08 | ✅ |
| [10](./phase-10-sandbox-base-image.md) | Sandbox base image | 2 · Sandbox & IDE | 01 | ✅ |
| [11](./phase-11-sandbox-manager.md) | Sandbox manager | 2 · Sandbox & IDE | 06, 10 | ✅ |
| [12](./phase-12-workspace-filesystem-service.md) | Workspace filesystem service | 2 · Sandbox & IDE | 11 | ✅ |
| [13](./phase-13-command-execution-terminal.md) | Command execution & terminal | 2 · Sandbox & IDE | 05, 11 | ✅ |
| [14](./phase-14-web-ide-monaco.md) | Web IDE (Monaco) + live code streaming | 2 · Sandbox & IDE | 09, 12 | ✅ |
| [15](./phase-15-live-preview.md) | Live preview | 2 · Sandbox & IDE | 11, 13 | ✅ |
| [16](./phase-16-designprovider-interface-registry.md) | DesignProvider interface & registry | 3 · Design | 08 | ✅ |
| [17](./phase-17-stitch-provider.md) | Stitch MCP provider | 3 · Design | 16 | ✅ |
| [18](./phase-18-figma-provider.md) | Figma MCP provider (alternative) | 3 · Design | 16 | ✅ |
| [19](./phase-19-design-stage-handler-ui.md) | Design stage handler & UI | 3 · Design | 09, 14, 16, 17 | ✅ |
| [20](./phase-20-anthropic-client-model-routing.md) | Anthropic client & model routing | 4 · Build | 07 | ✅ |
| [21](./phase-21-agent-tools-sandbox.md) | Agent tools bound to the sandbox | 4 · Build | 12, 13, 20 | ✅ |
| [22](./phase-22-app-skeleton-template.md) | Fixed-stack app skeleton template | 4 · Build | 10 | ✅ |
| [23](./phase-23-codegen-agent-loop.md) | Codegen agent loop | 4 · Build | 15, 19, 21, 22 | ✅ |
| [24](./phase-24-build-stage-handler-ui.md) | Build stage handler & UI | 4 · Build | 09, 23 | ✅ |
| [25](./phase-25-requirements-schema-api.md) | Structured requirements schema & API | 5 · Requirements & Tests | 07 | ✅ |
| [26](./phase-26-requirements-ui.md) | Requirements UI (guided form) | 5 · Requirements & Tests | 09, 25 | ✅ |
| [27](./phase-27-test-generation-agent.md) | Test generation agent | 5 · Requirements & Tests | 21, 25 | ✅ |
| [28](./phase-28-test-runner-result-parsing.md) | Test runner & result parsing | 5 · Requirements & Tests | 13, 27 | ✅ |
| [29](./phase-29-failure-analyzer-repair-context.md) | Failure analyzer & minimal repair context | 6 · Repair Loop | 12, 28 | ✅ |
| [30](./phase-30-diff-aware-repair-agent.md) | Diff-aware repair agent | 6 · Repair Loop | 21, 29 | ✅ |
| [31](./phase-31-loop-controller.md) | Loop controller (bounded + convergent) | 6 · Repair Loop | 30 | ✅ |
| [32](./phase-32-repair-test-ui.md) | Repair & test UI | 6 · Repair Loop | 09, 28, 31 | ✅ |
| [33](./phase-33-infra-analyzer.md) | Infra analyzer | 7 · Deploy | 12 | ✅ |
| [34](./phase-34-secrets-credential-vault.md) | Secrets & credential vault | 7 · Deploy | 03 | ✅ |
| [35](./phase-35-deploy-provider-adapters.md) | Deploy provider adapters (Vercel + Render) | 7 · Deploy | 33, 34 | ✅ |
| [36](./phase-36-db-provisioning.md) | DB provisioning | 7 · Deploy | 02, 34 | ✅ |
| [37](./phase-37-deploy-orchestration.md) | Deploy orchestration | 7 · Deploy | 35, 36 | ✅ |
| [38](./phase-38-deployment-topology.md) | Visual deployment topology (React Flow) | 7 · Deploy | 05, 37 | ✅ |
| [39](./phase-39-post-deploy-test-runner.md) | Post-deploy test runner | 8 · Live Validation | 28, 37 | ✅ |
| [40](./phase-40-live-failure-repair-path-ui.md) | Live-failure repair path & UI | 8 · Live Validation | 31, 38, 39 | ✅ |
| [41](./phase-41-per-project-data-api.md) | Per-project data API | 9 · Data Browser | 36 | ✅ |
| [42](./phase-42-data-browser-ui.md) | Data browser UI | 9 · Data Browser | 09, 41 | ✅ |
| [43](./phase-43-benchmark-spec-format-seeds.md) | Benchmark spec format & seeds | 10 · Eval | 25 | ✅ |
| [44](./phase-44-headless-pipeline-runner-metrics.md) | Headless pipeline runner & metrics | 10 · Eval | 23, 28, 31, 37, 43 | ✅ |
| [45](./phase-45-results-dashboard-report.md) | Results dashboard/report | 10 · Eval | 44 | ✅ |
| [46](./phase-46-observability-cost-tracking.md) | Observability & cost tracking | 11 · Hardening & Demo | 20 | ✅ |
| [47](./phase-47-security-sandbox-hardening.md) | Security & sandbox hardening pass | 11 · Hardening & Demo | 11, 34 | ✅ |
| [48](./phase-48-resilience-error-handling.md) | Resilience & error handling | 11 · Hardening & Demo | 08, 20, 35 | ✅ |
| [49](./phase-49-pwa-finalize.md) | PWA finalize | 11 · Hardening & Demo | 04 | ✅ |
| [50](./phase-50-demo-scenario-seed.md) | Demo scenario & seed | 11 · Hardening & Demo | all prior | ✅ |
| [51](./phase-51-layered-config-service.md) | Layered configuration service & platform settings store | 12 · Admin & Config ✚ | 01, 02, 03 | ✅ |
| [52](./phase-52-admin-dashboard-ui.md) | Admin dashboard UI | 12 · Admin & Config ✚ | 03, 04, 51 | ✅ |
| [53](./phase-53-openai-compatible-provider.md) | OpenAI-compatible LLM provider | 12 · Admin & Config ✚ | 20, 01, 51 | ✅ |
| [54](./phase-54-skeleton-demo-removal.md) | Skeleton demo removal & tool-surface hardening | 13 · Build Reliability ✚ | 21, 22, 23 | ✅ |
| [55](./phase-55-build-verification-selfheal.md) | Build verification & bounded self-heal | 13 · Build Reliability ✚ | 23, 24, 29, 30, 31 | ✅ |
| [56](./phase-56-phased-build-plan.md) | Phased build plan & phase-by-phase codegen | 13 · Build Reliability ✚ | 54, 55 | ✅ |
| [57](./phase-57-provider-param-compatibility.md) | Provider-agnostic request compatibility | 12 · Admin & Config ✚ | 53, 51, 20 | ✅ |
| [58](./phase-58-vercel-backend-deploy.md) | Vercel-hosted backend & deploy correctness | 7 · Deploy ✚ | 34, 35, 36, 37, 22 | ✅ |
| [59](./phase-59-preview-process-lifecycle.md) | Preview process lifecycle & port reclaim | 2 · Sandbox & IDE ✚ | 10, 11, 13, 15, 55, 56 | ⬜ |
| [60](./phase-60-scope-aware-build.md) | Scope-aware build & OpenAI-first posture | 13 · Build Reliability ✚ | 23, 53, 55, 56, 57 | ⬜ |
| [61](./phase-61-workspace-layout.md) | Workspace layout: resizable panels & focus mode | 1 · Orchestration Core ✚ | 09, 13, 14, 15 | ⬜ |

✚ *Epics 12 (phases 51–53, 57) and 13 (phases 54–56) were added per the user's directive — see "Additions beyond the roadmap" below.*

## Changelog (post-roadmap changes)

| Date | Change | Touches | Notes |
|------|--------|---------|-------|
| 2026-08-06 | **Stage order: design-first → requirements-first** (`IMPLEMENTATION_PLAN.md` D15) | [16](./phase-16-designprovider-interface-registry.md)–[19](./phase-19-design-stage-handler-ui.md) (Design), [25](./phase-25-requirements-schema-api.md)–[26](./phase-26-requirements-ui.md) (Requirements) | Requirements now precedes design in the nav, as the default entry stage of a new project, and in the staleness cascade. Design optionally folds a saved `RequirementSpec` into the design-provider prompt (soft — unchanged behavior without one), and the design panel shows a dismissible nudge toward requirements when they are untouched. A whole-spec AI draft endpoint (freeform description → editable feature list, Haiku, never auto-saved) was added next to the existing per-feature suggest. **No new hard prerequisites** — `deploy ⇐ build` and `validate ⇐ deploy` remain the only ones (D12). |
| 2026-08-15 | **Build stage: one press → a verified, working app** (new Epic 13, phases [54](./phase-54-skeleton-demo-removal.md)–[56](./phase-56-phased-build-plan.md)) | [22](./phase-22-app-skeleton-template.md)–[24](./phase-24-build-stage-handler-ui.md) (skeleton, codegen, build handler), [29](./phase-29-failure-analyzer-repair-context.md)–[31](./phase-31-loop-controller.md) (repair, now reused from Build) | Build plans the work in phases, implements them one at a time with a small per-phase context and its own typecheck gate, then verifies (install → typecheck → boot → placeholder check) and self-heals through the **existing** bounded repair loop. Sandbox/environment failures are classified and short-circuit with **zero** repair iterations; only `code`-class failures reach the agent. `complete` now requires typecheck clean **and** both dev servers healthy, not just a boot. The skeleton's demo UI no longer survives into generated apps. **All bounds preserved** — D7 / Golden Rule 5 hold, with per-phase, integration and wall-clock caps. **No new hard prerequisites** (D12). |

> **Why the live stage order does not match the phase numbering.** Phase numbers record the order the
> system was *built* (design in Epic 3, requirements in Epic 5); the stage order records the order a
> user *works*. They diverged with the 2026-08-06 reorder above. Shipped phase files are deliberately
> **not** renumbered — their numbers are cross-referenced from code comments, tests and other plans.

## Milestone map (from `IMPLEMENTATION_PLAN.md` §11)

| Milestone | Scope | Phases | Outcome |
|-----------|-------|--------|---------|
| **M1** ✅ | Epics 0–2 | 01–15 | Authenticated workspace + working web IDE / terminal / preview over sandboxes. |
| **M2** ✅ | Epics 3–4 | 16–24 | Screenshots/text → design → generated **running app** with live code + change requests. |
| **M3** ✅ | Epics 5–6 | 25–32 | Requirements → tests → **bounded self-healing repair loop** (the contribution) working. |
| **M4** ✅ | Epics 7–8 | 33–40 | Dual-mode deploy + visual topology + live validation → **deployed URL delivered & verified**. |
| **M5** ✅ | Epics 9–11 (+12) | 41–53 | Data browser, evaluation evidence, hardening, PWA polish, **hero demo**, admin configurability, and multi-provider LLM support. |

## Additions beyond the roadmap (user directive)

Three capabilities were requested that Section 10 did not enumerate. They are handled as follows:

1. **Runtime-configurable platform settings via an admin dashboard** (model name, provider, base URL, budgets, sandbox limits, repair thresholds, feature flags), with precedence **admin panel > env > default**. → New **Epic 12**: [`phase-51`](./phase-51-layered-config-service.md) (layered config service + platform settings store + admin API) and [`phase-52`](./phase-52-admin-dashboard-ui.md) (admin dashboard UI). The resolver *seam* is pre-built in `phase-01`, with the `PlatformSetting` collection in `phase-02` and the `admin` role in `phase-03`, so 51/52 are additive and low-risk. **Scope note (follow-up):** coverage is now *every* `Settings` field — all 108, across 15 sections — not a curated subset, enforced by `tests/config/test_registry_complete.py`. Secrets (API tokens, `SECRET_KEY`) are settable there as **write-only** values, Fernet-encrypted at rest and decrypted only at call time; `FERNET_KEY`, `MONGODB_URI` and `FORGEFLOW_META_DB` stay env-only (shown read-only with the reason) because they are read before the admin layer exists or protect the ciphertext itself.
2. **A pre-built React + Tailwind frontend template, always reused for every generated app** (so the LLM never rescaffolds the frontend, saving tokens). → Woven into [`phase-22`](./phase-22-app-skeleton-template.md) (the skeleton ships a complete Tailwind-configured FE + BE; instantiated by deterministic copy, never generated) and [`phase-23`](./phase-23-codegen-agent-loop.md) (codegen's first step is `instantiate_skeleton`; the agent writes only feature code), with an `instantiate_skeleton` tool in [`phase-21`](./phase-21-agent-tools-sandbox.md).

3. **Run the control-plane agents against OpenAI-compatible APIs/base URLs/models**, not only Anthropic. → New [`phase-53`](./phase-53-openai-compatible-provider.md): a second `Transport` (a pure OpenAI Chat Completions adapter) selected by `LLM_PROVIDER`, reusing the phase-20 seam so no agent/tool/cost code changes and Anthropic stays the default. This extends the §1 "anthropic SDK" decision, so it is documented rather than applied silently; every other locked decision (generated-app stack, bounded repair, cost discipline, secret handling) is preserved.

4. **One Build press must produce a *working* app, and the template must never appear in the final product.** The directive: *"the Build phase itself should build the entire project and keep fixing the product until it actually works, instead of me having to repeatedly tell the agent what is broken… if the terminal gives any code-based error it will be sent to [the agent]; if it is a sandbox env error it will not be forwarded — it will stop"*, plus *"I don't want the template to exist as a starting product or appear in the final app"*, plus a phase plan saved in its own workspace folder. → New **Epic 13**: [`phase-54`](./phase-54-skeleton-demo-removal.md) (the skeleton's demo UI stops being advertised to the model as finished work — the actual cause — and the codegen loop can no longer rescaffold itself), [`phase-55`](./phase-55-build-verification-selfheal.md) (install → typecheck → boot → placeholder check, then self-heal through the **existing** repair loop, with a code-vs-environment classifier that short-circuits sandbox failures before they ever reach the agent), and [`phase-56`](./phase-56-phased-build-plan.md) (a structured phase plan in `phase-plan/`, implemented one phase at a time with a small context and a typecheck gate each).

   **No locked decision is deviated from.** The repair loop stays bounded — `phase-55` *reuses* the phase-31 controller rather than writing a second one, and adds per-phase, integration and wall-clock caps (D7 / Golden Rule 5). The skeleton is still instantiated by deterministic copy and never model-generated (the phase-22/23 directive above); `templates/app-skeleton/` is not edited at all, since the user chose prompt-only removal backed by a deterministic check. Planning still routes to Haiku and codegen to Sonnet (D4 / Golden Rule 7). Artifacts stay versioned rather than overwritten (§7) — the phase plan is a new `meta.kind` under the existing `code_change` type, since `ArtifactType` is fixed by §6. And `deploy ⇐ build` / `validate ⇐ deploy` remain the only hard prerequisites (D12): the phase plan has a deterministic fallback so Build still runs with requirements-only, design-only, or neither.

5. **A model call must not fail because one provider spells the request differently.** Reported against `gpt-5.4-mini` on the official OpenAI endpoint: `400 Unsupported parameter: 'max_tokens' is not supported with this model. Use 'max_completion_tokens' instead.` The directive: *"ensure that these kind of errors dont occur in the future not just for max_tokens param but for other params as well… find out a way to make this llm provider agnostic and not coded according to only one llm provider."* → New [`phase-57`](./phase-57-provider-param-compatibility.md) in Epic 12: the transport reads the provider's **own** 400 (which names the offending parameter, and often its replacement), repairs the body — rename, drop, `system`→`developer`, or fall back to non-streaming — retries, and remembers the working shape per `(endpoint, model)` in the phase-51 settings store. `phase-53`'s bespoke `stream_options` special case collapses into this one mechanism rather than sitting beside it.

   **No locked decision is deviated from.** `MessageRequest` and the `Transport` protocol are unchanged, so the Anthropic path is untouched and the phase-20 seam still means no agent/tool/cost code knows which provider is in use. Every ladder stays bounded (D7 / Golden Rule 5): repairs are capped, must be *novel*, and are counted separately from the transient-retry budget. A rename never degrades to a drop for an output cap, so budget accounting (D13) cannot be silently uncapped. Secrets are untouched (§7), and persistence is fail-soft — a failed write costs re-learning, never a broken call.

6. **Deploy must actually ship the generated app; Build must not re-plan for a one-line fix; the preview must survive a restart; the workspace must be usable.** Four issues reported from a live session, 2026-08-23. → [`phase-58`](./phase-58-vercel-backend-deploy.md) (the backend leg could never work — Render clones a git repo and nothing ever pushed the workspace anywhere; the backend moves to **Vercel**, which takes the same inline file upload the frontend already uses, so source delivery needs no git host, no new credential and no push plumbing — plus the partial-failure guard that let a dead backend ship a live frontend), [`phase-59`](./phase-59-preview-process-lifecycle.md) (orphaned dev servers squatting on 5173/3001 across a `--reload`, ports reclaimed deterministically instead of from remembered PIDs, and PID-budget exhaustion classified as an environment failure), [`phase-60`](./phase-60-scope-aware-build.md) (a cheap classifier routes a refine to a single incremental pass instead of a full re-plan, on a separately configurable `MODEL_CLASSIFY`), and [`phase-61`](./phase-61-workspace-layout.md) (resizable, collapsible, persisted panels plus a full-window focus mode, replacing the fixed three-column grid).

   **One locked decision is amended, with explicit authorisation.** **D11** changes from *FE→Vercel, BE→Render* to *FE→Vercel, BE→Vercel*: its stated rationale — *"Vercel alone can't host a persistent Node backend"* — no longer holds, and the alternative (a GitHub repo per project, pushed from the control plane) buys a persistent process at the cost of a new provider, a new credential kind and per-project repo lifecycle. The user was asked and chose Vercel (Golden Rule 1). Render is retained behind `DEPLOY_BE_PROVIDER` rather than deleted. **D4** is annotated rather than changed: cost discipline still routes classification/summaries to the cheap model and code to the capable one, but the project now records **OpenAI-compatible Chat Completions as the primary interface** with Anthropic fully supported behind the same `phase-53` seam — a documentation correction, since `phase-57` already removed the last vendor-shaped assumption from the request. Every other decision holds: the generated-app stack (D2) is unchanged, untrusted code still never runs in the control plane (the workspace is *read* via the archive API), every loop stays bounded (D7 / Golden Rule 5), and `deploy ⇐ build` / `validate ⇐ deploy` remain the only hard prerequisites (D12).

All six are recorded under **Design notes** in their phase files. Five remain faithful to every locked decision; the sixth amends **D11** with explicit user authorisation, as recorded above.
