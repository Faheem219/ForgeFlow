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
| [04](./phase-04-frontend-shell-pwa.md) | Frontend app shell & PWA base | 0 · Foundations | 01 | ⬜ |
| [05](./phase-05-realtime-hub.md) | Realtime event hub | 0 · Foundations | 03 | ⬜ |
| [06](./phase-06-project-lifecycle-state-machine.md) | Project lifecycle & stage state machine | 1 · Orchestration Core | 02, 03 | ⬜ |
| [07](./phase-07-conversation-artifact-model.md) | Conversation & artifact model | 1 · Orchestration Core | 06 | ⬜ |
| [08](./phase-08-conductor.md) | Conductor (orchestrator) | 1 · Orchestration Core | 05, 06, 07 | ⬜ |
| [09](./phase-09-workspace-ui-shell.md) | Workspace UI shell | 1 · Orchestration Core | 04, 05, 08 | ⬜ |
| [10](./phase-10-sandbox-base-image.md) | Sandbox base image | 2 · Sandbox & IDE | 01 | ⬜ |
| [11](./phase-11-sandbox-manager.md) | Sandbox manager | 2 · Sandbox & IDE | 06, 10 | ⬜ |
| [12](./phase-12-workspace-filesystem-service.md) | Workspace filesystem service | 2 · Sandbox & IDE | 11 | ⬜ |
| [13](./phase-13-command-execution-terminal.md) | Command execution & terminal | 2 · Sandbox & IDE | 05, 11 | ⬜ |
| [14](./phase-14-web-ide-monaco.md) | Web IDE (Monaco) + live code streaming | 2 · Sandbox & IDE | 09, 12 | ⬜ |
| [15](./phase-15-live-preview.md) | Live preview | 2 · Sandbox & IDE | 11, 13 | ⬜ |
| [16](./phase-16-designprovider-interface-registry.md) | DesignProvider interface & registry | 3 · Design | 08 | ⬜ |
| [17](./phase-17-stitch-provider.md) | Stitch MCP provider | 3 · Design | 16 | ⬜ |
| [18](./phase-18-figma-provider.md) | Figma MCP provider (alternative) | 3 · Design | 16 | ⬜ |
| [19](./phase-19-design-stage-handler-ui.md) | Design stage handler & UI | 3 · Design | 09, 14, 16, 17 | ⬜ |
| [20](./phase-20-anthropic-client-model-routing.md) | Anthropic client & model routing | 4 · Build | 07 | ⬜ |
| [21](./phase-21-agent-tools-sandbox.md) | Agent tools bound to the sandbox | 4 · Build | 12, 13, 20 | ⬜ |
| [22](./phase-22-app-skeleton-template.md) | Fixed-stack app skeleton template | 4 · Build | 10 | ⬜ |
| [23](./phase-23-codegen-agent-loop.md) | Codegen agent loop | 4 · Build | 15, 19, 21, 22 | ⬜ |
| [24](./phase-24-build-stage-handler-ui.md) | Build stage handler & UI | 4 · Build | 09, 23 | ⬜ |
| [25](./phase-25-requirements-schema-api.md) | Structured requirements schema & API | 5 · Requirements & Tests | 07 | ⬜ |
| [26](./phase-26-requirements-ui.md) | Requirements UI (guided form) | 5 · Requirements & Tests | 09, 25 | ⬜ |
| [27](./phase-27-test-generation-agent.md) | Test generation agent | 5 · Requirements & Tests | 21, 25 | ⬜ |
| [28](./phase-28-test-runner-result-parsing.md) | Test runner & result parsing | 5 · Requirements & Tests | 13, 27 | ⬜ |
| [29](./phase-29-failure-analyzer-repair-context.md) | Failure analyzer & minimal repair context | 6 · Repair Loop | 12, 28 | ⬜ |
| [30](./phase-30-diff-aware-repair-agent.md) | Diff-aware repair agent | 6 · Repair Loop | 21, 29 | ⬜ |
| [31](./phase-31-loop-controller.md) | Loop controller (bounded + convergent) | 6 · Repair Loop | 30 | ⬜ |
| [32](./phase-32-repair-test-ui.md) | Repair & test UI | 6 · Repair Loop | 09, 28, 31 | ⬜ |
| [33](./phase-33-infra-analyzer.md) | Infra analyzer | 7 · Deploy | 12 | ⬜ |
| [34](./phase-34-secrets-credential-vault.md) | Secrets & credential vault | 7 · Deploy | 03 | ⬜ |
| [35](./phase-35-deploy-provider-adapters.md) | Deploy provider adapters (Vercel + Render) | 7 · Deploy | 33, 34 | ⬜ |
| [36](./phase-36-db-provisioning.md) | DB provisioning | 7 · Deploy | 02, 34 | ⬜ |
| [37](./phase-37-deploy-orchestration.md) | Deploy orchestration | 7 · Deploy | 35, 36 | ⬜ |
| [38](./phase-38-deployment-topology.md) | Visual deployment topology (React Flow) | 7 · Deploy | 05, 37 | ⬜ |
| [39](./phase-39-post-deploy-test-runner.md) | Post-deploy test runner | 8 · Live Validation | 28, 37 | ⬜ |
| [40](./phase-40-live-failure-repair-path-ui.md) | Live-failure repair path & UI | 8 · Live Validation | 31, 38, 39 | ⬜ |
| [41](./phase-41-per-project-data-api.md) | Per-project data API | 9 · Data Browser | 36 | ⬜ |
| [42](./phase-42-data-browser-ui.md) | Data browser UI | 9 · Data Browser | 09, 41 | ⬜ |
| [43](./phase-43-benchmark-spec-format-seeds.md) | Benchmark spec format & seeds | 10 · Eval | 25 | ⬜ |
| [44](./phase-44-headless-pipeline-runner-metrics.md) | Headless pipeline runner & metrics | 10 · Eval | 23, 28, 31, 37, 43 | ⬜ |
| [45](./phase-45-results-dashboard-report.md) | Results dashboard/report | 10 · Eval | 44 | ⬜ |
| [46](./phase-46-observability-cost-tracking.md) | Observability & cost tracking | 11 · Hardening & Demo | 20 | ⬜ |
| [47](./phase-47-security-sandbox-hardening.md) | Security & sandbox hardening pass | 11 · Hardening & Demo | 11, 34 | ⬜ |
| [48](./phase-48-resilience-error-handling.md) | Resilience & error handling | 11 · Hardening & Demo | 08, 20, 35 | ⬜ |
| [49](./phase-49-pwa-finalize.md) | PWA finalize | 11 · Hardening & Demo | 04 | ⬜ |
| [50](./phase-50-demo-scenario-seed.md) | Demo scenario & seed | 11 · Hardening & Demo | all prior | ⬜ |
| [51](./phase-51-layered-config-service.md) | Layered configuration service & platform settings store | 12 · Admin & Config ✚ | 01, 02, 03 | ⬜ |
| [52](./phase-52-admin-dashboard-ui.md) | Admin dashboard UI | 12 · Admin & Config ✚ | 03, 04, 51 | ⬜ |

✚ *Epic 12 (phases 51–52) was added per the user's directive — see "Additions beyond the roadmap" below.*

## Milestone map (from `IMPLEMENTATION_PLAN.md` §11)

| Milestone | Scope | Phases | Outcome |
|-----------|-------|--------|---------|
| **M1** | Epics 0–2 | 01–15 | Authenticated workspace + working web IDE / terminal / preview over sandboxes. |
| **M2** | Epics 3–4 | 16–24 | Screenshots/text → design → generated **running app** with live code + change requests. |
| **M3** | Epics 5–6 | 25–32 | Requirements → tests → **bounded self-healing repair loop** (the contribution) working. |
| **M4** | Epics 7–8 | 33–40 | Dual-mode deploy + visual topology + live validation → **deployed URL delivered & verified**. |
| **M5** | Epics 9–11 (+12) | 41–52 | Data browser, evaluation evidence, hardening, PWA polish, **hero demo**, and admin configurability. |

## Additions beyond the roadmap (user directive)

Two capabilities were requested that Section 10 did not enumerate. They are handled as follows:

1. **Runtime-configurable platform settings via an admin dashboard** (model name, provider, base URL, budgets, sandbox limits, repair thresholds, feature flags), with precedence **admin panel > env > default**. → New **Epic 12**: [`phase-51`](./phase-51-layered-config-service.md) (layered config service + platform settings store + admin API) and [`phase-52`](./phase-52-admin-dashboard-ui.md) (admin dashboard UI). The resolver *seam* is pre-built in `phase-01`, with the `PlatformSetting` collection in `phase-02` and the `admin` role in `phase-03`, so 51/52 are additive and low-risk. Sensitive keys (API tokens) route through the encrypted vault (`phase-34`), not plain settings.
2. **A pre-built React + Tailwind frontend template, always reused for every generated app** (so the LLM never rescaffolds the frontend, saving tokens). → Woven into [`phase-22`](./phase-22-app-skeleton-template.md) (the skeleton ships a complete Tailwind-configured FE + BE; instantiated by deterministic copy, never generated) and [`phase-23`](./phase-23-codegen-agent-loop.md) (codegen's first step is `instantiate_skeleton`; the agent writes only feature code), with an `instantiate_skeleton` tool in [`phase-21`](./phase-21-agent-tools-sandbox.md).

Both are recorded under **Design notes** in their phase files and remain faithful to the locked decisions (fixed generated-app stack, cost discipline, secret handling).
