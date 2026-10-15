# ForgeFlow: project facts and claim verification for the Springer manuscript

Prepared by the Content Author (Phase 1), 15 Oct 2026. Read-only verification: no draft or project
file was changed.

**Sources and reference points**

- Draft: `paper/IEEE Conference Paper Draft - Revised 4 Oct 2026.docx`, via
  `review/draft-reference.md` (section and table numbering below follow the draft).
- Repository snapshot used for dated claims: **`c5a7835`** (2026-10-04 14:39 IST, the last commit
  on 4 Oct). `backend/`, `eval/` and `templates/` are byte-identical between `c5a7835` and HEAD
  (`aeede9d`). `frontend/` changed later (UI revamp, 14 Oct), so frontend figures were measured at
  `c5a7835` in a temporary worktree.
- Evaluation artifacts (local, **gitignored** by `.gitignore:119-120`):
  `eval/results/bench/bench-1790941973.json` (Level 1, 2026-10-02 10:02-11:52 UTC) and
  `eval/results/eval-1790946244.json` (Level 2, 12:13-12:58 UTC). Tracked summary:
  `eval/results/REPORT.md`. The two Level-2 projects (`eval-notes-crud`
  `6abfa0060402248d8a0736e3`, `eval-bookmark-manager` `6abfa00626c635e3f72100fb`) are still in
  the local `forgeflow_meta` MongoDB, with their test runs and repair reports.
- Every Table IV-VI number and every statistic in Sec. VIII was recomputed from these files
  (Wilson 95% intervals, exact two-sided McNemar, median and p95 with linear interpolation).

---

## (a) Project summary

**What it is.** ForgeFlow is an AI-native, human-in-the-loop PWA. It takes a web-app idea
(screenshots or a structured spec) through six stages: requirements, design, build, test with
bounded self-healing repair, deploy, and live validation. The user can refine, revisit, skip or
reset any stage. The locked decisions are D1-D15 in `IMPLEMENTATION_PLAN.md` §1. The research
contribution (D1/D7) is the bounded, diff-aware, escalating repair loop inside a non-linear
orchestration. The secondary contribution is infrastructure-aware, dual-mode deployment with
live validation.

**Architecture (3 planes plus providers).**
- *Control plane:* FastAPI on Python 3.11 (`backend/pyproject.toml`), Pydantic v2 and
  Motor/Beanie on MongoDB. Metadata lives in `forgeflow_meta`, and each project gets its own app
  database (D8). The control plane holds auth, projects and the stage state machine
  (`app/projects/state_machine.py`), the conductor (`app/orchestrator/conductor.py`), the stage
  handlers, the agents (`app/agents/*`), the sandbox manager, deploy (`app/deploy/*`), the Fernet
  credential vault (`app/deploy/secrets.py`), the eval harness (`app/eval/*`) and a WebSocket hub
  (`app/realtime/hub.py`).
- *LLM access* goes through one transport seam. Anthropic and OpenAI-compatible Chat Completions
  are both supported (phase-20/53/57). Routing is by role (`app/agents/models.py`):
  - `MODEL_CODEGEN` (default `claude-sonnet-5`) for codegen, testgen and repair.
  - `MODEL_ROUTING` (default `claude-haiku-4-5-20251001`) for summaries and drafts.
  - `MODEL_CLASSIFY` (blank falls back to routing).
  - Budget caps are per project and global (`app/agents/budget.py`).
- *Execution plane:* one Docker sandbox per project (`app/sandbox/manager.py:139-195`). It runs
  as a non-root user (`sandbox/Dockerfile:114,134`), with `cap_drop=ALL`, CPU, memory and PID
  limits, and its own `internal` network (no host route, no internet), and is reaped when idle.
  It holds the git-backed workspace, the dev servers behind a reverse-proxied preview, and the
  test runners. Generated apps use a fixed stack: React+Vite+TS, Express+TS, Mongoose, and
  Vitest/Jest+supertest/Playwright. They start from a 53-file skeleton
  (`templates/app-skeleton`, `git ls-files` = 53) that is copied deterministically
  (`app/agents/tools/skeleton.py`).
- *Presentation plane:* React 18 PWA (`frontend/src/features/*`). It has the dashboard,
  workspace/stage navigator, Monaco IDE, xterm.js terminal, preview, test+repair panel, React
  Flow topology (`@xyflow/react`), data browser, admin and eval dashboard.
- *Realtime:* `Event{event, project_id, stage, payload, seq, ts}` (`app/realtime/schemas.py:53-59`).
  Each project has its own monotonic `seq` and a replay ring buffer (`hub.py:23-50`).

**Stage model and conductor.**
- `STAGE_ORDER` = requirements, design, build, test, deploy, validate (`state_machine.py:70-77`).
  It drives the navigator, the entry stage and the staleness cascade only. Statuses are
  `{empty, in_progress, awaiting_user, complete, skipped, stale}` (`db/models/enums.py:39-46`).
- The pure state machine (`state_machine.py:1-67`) has these actions:
  - `enter` and `skip`;
  - `complete`;
  - `refine`, which reopens a stage and marks non-empty downstream stages `stale` without
    deleting anything;
  - `unskip`, which restores the pre-skip status;
  - `reset`: build/test/requirements/design go from `complete` to `awaiting_user`, and
    deploy/validate go back to `empty` with the deployment torn down.
- The only hard prerequisites are deploy⇐build (build `complete`) and validate⇐deploy.
- The conductor owns legality. An intent is `{stage, action, message, payload}` with
  `action ∈ {refine, proceed, skip, unskip, reset}` (`app/orchestrator/schemas.py:80-127`).
- The conductor has real handlers for design, build, deploy and validate
  (`stages/__init__.py:65-77`). Requirements and test run through their own routers (requirements
  API; tests/repair API).

**Traceability chain (criterion_id).**
1. `AcceptanceCriterion.id` is a stable join key (`db/models/requirement.py:15-35`).
2. Testgen tags each test title with `[<id>]`.
3. The parser recovers it (`testing/parsers.py:24-35`) into `TestResult.criterion_id`. Results
   from Vitest, Jest and Playwright **JSON** reporters are normalised into one shape
   (`testing/models.py`).
4. The repair context carries `FailingTest.criterion_id` and joins `RequirementSnippet` by id
   (`agents/repair_context.py:243-262`).
5. The escalation lists `criterion_id` per failing test (`stages/repair.py:492-500`).
6. The eval grades each criterion: "met" means at least one tagged test passed and none failed,
   and an untested criterion counts as unmet (`eval/runner.py:116-134`).

**Repair loop.**
- *Context* (`agents/repair_context.py`) is deterministic and model-free. It holds:
  - the failing tests (name, framework, criterion_id, file, message, assertion, stack,
    files_referenced);
  - the sources they implicate (test file plus stack-named files, ranked by frequency);
  - the git diff since the `refs/forgeflow/last-passing` ref, which the test runner advances on
    every green run (`testing/runner.py:176-179`);
  - the requirement snippets.
- *Context caps:* 24 000 / 8 000 per file / 8 000 for the diff (`core/config.py:364-366`). Files
  and diff are truncated to their head; a file that would exceed the total is dropped; each
  truncation or drop is recorded in `trimmed`.
- *Test-stage widening:* the Test-stage Repair action (`testing/repair_router.py:107`) wraps this
  in `SuiteRepairContextAnalyzer` (`agents/suite_context.py`). Per failing test it widens in tiers:
  1. same-stem sibling;
  2. direct imports;
  3. folder;
  4. transitive imports, via a BFS bounded at depth 3 / 24 files (`agents/import_graph.py`,
     `config.py:369-370`); bare package specifiers never resolve;
  5. files changed since last-passing;
  6. the suite's `src` tree.
- *Context per entry point:*
  - Build self-heal uses `BuildRepairContextAnalyzer` (`build_verify.py:786-790`).
  - **The eval harness (`eval/runner.py:195`) and live validation (`stages/validate.py:243`)
    use the base analyzer without widening.**
- *Patch agent* (`agents/repair.py`):
  - Its tools are only `read_file`, `write_file` and `git_commit`, plus a bounded `request_file`
    (≤3 non-test source files per attempt, `config.py:373`).
  - Writes outside the editable set are refused, and test files are read-only (the oracle).
  - It always commits, re-runs the affected suite and then the full suite, and computes
    newly_failing/newly_passing.
- *Controller* (`orchestrator/stages/repair.py:236-407`):
  1. Per iteration it checks cancellation, then the environment diagnosis (escalates before any
     model call, at zero tokens), then makes the agent call. A budget `UserError` escalates, and
     "nothing editable" escalates as `blocked`.
  2. After the attempt it checks green, then regression ≥2, then no-patch (2 consecutive empty
     attempts), then stall ≥2 (the failing count did not shrink).
  3. When the loop is exhausted it escalates `cap_reached` (default 5).
  4. A strictly-worse attempt is git-reverted: it regressed, did not shrink the failing count and
     is not green.
- *Escalation* sets Test to `awaiting_user` and emits `repair.escalation{reason, summary,
  failing_tests, diffs_tried, metrics, resume={build, refine}}`.
- *Audit:* `RepairAttempt{iteration, target_files, diff_ref, resulting_run_id, outcome
  ∈{fixed,no_progress,regressed}, reverted}`; a costed `Run` with outcome `fixed|escalated`; a
  versioned `repair_report` artifact (ConvergenceMetrics); and per-attempt `repair.iteration`
  events.

**Deployment and live validation.**
- The infra analyzer (`deploy/analyzer.py`) is static and model-free. It produces an `infra_plan`
  (static FE / persistent BE / DB, plus env vars and secrets).
- The orchestrator (`deploy/orchestrate.py:100-207`) runs in this order:
  1. a pre-flight that refuses a localhost or docker-internal DB URI before any provider call
     (`db_provision.py:107-130`);
  2. provision the DB;
  3. backend to Vercel as a function from an inline upload, with `MONGODB_URI` set (Render via
     `DEPLOY_BE_PROVIDER=render`);
  4. build the FE with `VITE_API_BASE_URL` and deploy it to Vercel, but only if the BE is live;
  5. poll each target until live, and record the topology.
- The credential vault encrypts with Fernet, decrypts at call time, and never logs or returns a
  secret.
- Live validation (`stages/validate.py`) runs the Playwright suite against the live URL
  (`testing/live.py`) and classifies failures as env or code. Code failures go to the repair
  loop and then a redeploy, bounded by `validate_max_cycles` = 2 (`config.py:202`).

**Evaluation design.**
- *Level 1* (`app/eval/bench/*`) runs HumanEval 164, MBPP-sanitized test split 257,
  MultiPL-E HumanEval-TS 159, and BFCL v3 non-live (4 AST categories, 1 000 cases, plus 240
  irrelevance cases).
  - All go through the product transport, with one model (`z-ai/glm-5.3-flash` via OpenRouter,
    upstream pinned, `allow_fallbacks=False`, `anthropic_client.py:1033-1040`).
  - Temperature is 0 for code (BFCL 0.001). Code suites run 8 problems at a time; BFCL runs 12.
  - The repair arm gets the task, the code and the last 1 500 chars of output; it stops at
    5 attempts or after 2 stalls (same failure, or resubmitted code).
  - Graders self-check: 426/426 references pass and 580/580 stubs are rejected.
  - Model code runs in a no-network, cap-dropped, read-only-root container
    (`bench/executor.py`).
- *Level 2* (`app/eval/runner.py`) runs the spec corpus (`eval/specs/*.yaml`, 10 specs) through
  requirements, build, testgen, test and repair, each spec in its own project/sandbox/DB.
  - The recorded run started four specs at once (`--jobs 4`: notes-crud, todo-list, url-shortener
    and bookmark-manager, the first four in easiest-first corpus order).
  - It was stopped after notes-crud and bookmark-manager completed.

**Delivery of the platform.**
- CI is `.github/workflows/ci.yml` with 6 jobs: backend, config-guard, sandbox, security,
  frontend, skeleton. CD (`cd.yml`) deploys to an Oracle ARM VM and Vercel.
- A k3s demo (`docs/devops/evidence/k3s-rolling-update-verification.txt`) recorded `ok=807
  fail=0` at a 0.2 s probe interval. The broken release was auto-rolled back and served 0 requests
  (`docs/devops/README.md:162`).

---

## (b) Claim-verification table

Status key:
- **Confirmed**: the repository matches the claim.
- **Dated-but-true**: true as of the stated date.
- **Discrepancy**: see (d).
- **Unverifiable**: no artifact in the repository.

| # | Draft claim (section) | Evidence | Status |
|---|---|---|---|
| 1 | ~54 000 lines of production code (Abs.; Table II 53 878) | `c5a7835`: backend/app 175 `.py` = 35 426 lines; frontend/src non-test 133 = 18 452 | Confirmed |
| 2 | 2 323 automated tests (Abs.; Table II 1 815 / 234 files; 508 / 61 files) | `c5a7835`: pytest collects 1 932 in 234 files; `vitest list` 518 in 61 files (= 2 450). 1 815 + 508 is exactly the count at `4434e7e` (2 Oct 11:13 IST, 223 backend test files) | **Discrepancy D1** |
| 3 | pass@1 97.0/96.1/97.5 → 99.4/98.8/98.7 (Abs., Table IV) | bench JSON, recomputed | Confirmed |
| 4 | 13/19 repaired within two attempts, exact McNemar p<0.001, +5.7% calls, other six stopped before cap (Abs.) | repaired iterations {1:10, 2:3}; p = 2^-12·2 = 0.00024; 613 vs 580 calls; unrepaired stopped at 2,2,4,3,2,4 | Confirmed |
| 5 | 12/16 criteria met; every unmet criterion an E2E test (Abs., VIII-E) | eval JSON `criteria` 5/7, 7/9; DB failing tests are all Playwright | Confirmed |
| 6 | Hand-written 53-file skeleton copied, not generated (P2) | `git ls-files templates/app-skeleton` = 53; `agents/tools/skeleton.py:1-10` | Confirmed |
| 7 | Codegen role default claude-sonnet-5; routing role default claude-haiku-4-5 (P3) | `core/config.py:89-90` (id is `claude-haiku-4-5-20251001`) | Confirmed |
| 8 | Routing role does classification, summaries, freeform-to-feature-list draft (P3, IV-A) | `agents/models.py:39-53`; `orchestrator/requirements_draft.py:10-13,216` | Confirmed |
| 9 | Token usage per project; per-project and global caps halt with an actionable message (P3) | `agents/budget.py:1-7`; `config.py:94-95` | Confirmed |
| 10 | Any OpenAI-compatible endpoint without touching agent/tool/cost code (P3) | phase-53/57 transport; `agents/models.py:9-11` | Confirmed |
| 11 | Eval uses z-ai/glm-5.3-flash in both roles (P3, VIII-A) | REPORT.md; bench JSON `model` | Confirmed |
| 12 | Artifacts versioned, never overwritten (P4) | `ArtifactService.create_version`; IMPLEMENTATION_PLAN §7 | Confirmed |
| 13 | Sandbox: non-root, no host network, dropped caps, CPU/mem/PID limits, idle reaping (P5) | `sandbox/manager.py:6-11,139-195`; `sandbox/Dockerfile:114,134` | Confirmed |
| 14 | FastAPI/Py 3.11/Pydantic v2/Motor+Beanie; per-project DB + forgeflow_meta (III-B) | `backend/pyproject.toml:5-16`; `config.py` `forgeflow_meta_db` | Confirmed |
| 15 | Control plane includes "the six stage handlers" (III-B) | `orchestrator/stages/__init__.py:65-77`: 4 real handlers plus stubs; requirements/test logic sits in its own routers | Confirmed (nuance; no change needed) |
| 16 | Event {event, project_id, stage, payload, seq}; resume from seq (III-B) | `realtime/schemas.py:53-59` (+`ts`); `realtime/hub.py:32-50` | Confirmed |
| 17 | Order requirements→…→validate drives navigator/entry/staleness, gates nothing (III-C) | `projects/state_machine.py:1-30,70-77`; D15 | Confirmed |
| 18 | StageState statuses {empty, in_progress, awaiting_user, complete, skipped, stale} (III-C) | `db/models/enums.py:39-46` | Confirmed |
| 19 | Skip tolerated; testgen without requirements warns and offers capture/infer, does not refuse (III-C) | `agents/testgen.py:77-80,222-243,304-305` | Confirmed |
| 20 | Refine: new round and versions; downstream marked stale, not deleted (III-C) | `state_machine.py` truth table (refine row) | Confirmed |
| 21 | Intent {message, target_stage, action ∈ refine \| proceed \| skip} (III-C) | `orchestrator/schemas.py:80-115`: `{stage, action, message, payload}`, actions refine/proceed/skip/unskip/reset | **Discrepancy D4** (minor) |
| 22 | Only deploy⇐build and validate⇐deploy enforced (Contrib., III-C) | `state_machine.py:27-33`; `tests/orchestrator/test_conductor_legality.py:86` | Confirmed |
| 23 | Spec passed to the design prompt as a soft nudge, not a gate (III-C) | IMPLEMENTATION_PLAN D15 decision log; plans/README changelog 2026-08-06 | Confirmed |
| 24 | RequirementSpec feature = name, description, inputs, expected behaviours, acceptance criteria (IV-A) | `db/models/requirement.py:28-35` | Confirmed |
| 25 | Two entry points; AI draft never auto-saved (IV-A) | `requirements_draft.py:12-14` | Confirmed |
| 26 | Each test carries its criterion id; Vitest+TL / Jest+supertest / Playwright vs preview (IV-B) | `testing/parsers.py:24-35`; plan phase-27 | Confirmed |
| 27 | "The output of the JUnit and JSON reporters are normalised" (IV-C) | `testing/parsers.py:194-210` parses Vitest/Jest/Playwright **JSON** only; no JUnit parser exists (`grep -i junit app` = none) | **Discrepancy D5** (minor) |
| 28 | Normalised record {status, assertion, stack, files_referenced, criterion_id} (IV-C) | `testing/models.py:29-48` (also name, framework, file, duration, message) | Confirmed (subset) |
| 29 | Live run produces the same shape (IV-C, VI-C) | `testing/live.py` reuses `parse` and `PLAYWRIGHT_REPORT` | Confirmed |
| 30 | Context assembly deterministic, model-free, exhaustively testable (V-A) | `agents/repair_context.py:1-20` | Confirmed |
| 31 | Exactly four admitted parts (failing tests; their sources; diff since last-passing; criterion snippets) (V-A) | `repair_context.py:192-262` | Confirmed |
| 32 | Import-graph BFS depth 3 and 24 files; package imports never resolve (V-A) | `agents/import_graph.py:66-122,138-152`; `config.py:369-370`. Applied only by the Test-stage analyzer (`repair_router.py:107`) | Confirmed (scope: see D3) |
| 33 | At most three further non-test files per attempt (V-A) | `agents/repair.py:263-268,353-413`; `config.py:373` | Confirmed |
| 34 | Last-passing ref moved forward on each green run (V-A) | `testing/runner.py:176-179`; `sandbox/git.py:17,101` | Confirmed |
| 35 | Caps 24 000 / 8 000 / 8 000, runtime-configurable (V-A) | `config.py:364-366`; registry (admin-editable) | Confirmed |
| 36 | Oversize parts "simply dropped", "number of times they are dropped is recorded" (V-A) | `repair_context.py:237-240,298-306`: truncation to head; only over-budget files dropped; each item recorded by name | **Discrepancy D6** (minor) |
| 37 | Patch committed; affected suite then full suite (V-B) | `agents/repair.py:296-313,524-533` | Confirmed |
| 38 | Regression guard default 2, checked first; a single regression tolerated (V-C) | `stages/repair.py:73-74,379-383`; `config.py:360` | Confirmed |
| 39 | Attempt that broke something and fixed nothing is reverted (V-C) | `stages/repair.py:326-330,364-369,409-434` ("fixed nothing" = failing count did not shrink) | Confirmed |
| 40 | Stall guard 2; same size counts as no progress; any shrink resets (V-C) | `stages/repair.py:393-396`; `config.py:357` | Confirmed |
| 41 | Iteration cap 5, checked before each model call (V-C) | `stages/repair.py:282,397-399`; `config.py:356` | Confirmed |
| 42 | Budget cap, cancel, nothing-safe-to-patch, two consecutive empty attempts end the loop; env fault escalates before any model call at zero tokens (V-C) | `stages/repair.py:283-320,387-391`; `config.py:376` | Confirmed |
| 43 | Guards evaluated in the listed precedence (regression, stall, cap, then budget/cancel/blocked/empty) (V-C) | Code order after an attempt: regression → **no-patch** → stall; cap after the loop (`stages/repair.py:379-399`) | **Discrepancy D7** (minor) |
| 44 | RepairAttempt.outcome ∈ {fixed, no_progress, regressed}, regression dominates (V-C) | `enums.py:90-93`; `stages/repair.py:538-549` | Confirmed |
| 45 | Escalation: awaiting_user, `repair.escalation` with reason, summary, failing tests, diffs tried, metrics, resume (refine Build) (V-D) | `stages/repair.py:76-82,135-154,457-464` | Confirmed |
| 46 | RepairAttempt holds target files, diff ref, resulting run, reverted; loop is one costed Run with outcome {fixed, escalated} (V-E) | `db/models/repair_attempt.py:15-29`; `db/models/run.py:46-62`; `stages/repair.py:473-475` | Confirmed |
| 47 | Report fields initial_failing … wall_clock_s incl. "empty attempts" (V-E) | `stages/repair.py:103-132` (`noop_attempts`) | Confirmed |
| 48 | `repair.iteration {i, failing_count, regressions, tokens}` (V-E) | `stages/repair.py:350-362` (+outcome, files) | Confirmed |
| 49 | infra_plan: static FE / FE+persistent BE / FE+BE+DB, with env vars and secrets (VI-A) | `deploy/analyzer.py:1-17,34-42,95,117` | Confirmed |
| 50 | Seamless vs BYO; Fernet at rest; decrypted at call time; never logged or returned (VI-B) | `deploy/secrets.py:1-22` | Confirmed |
| 51 | Both tiers deploy SPA and Express function to Vercel from one inline upload; Render optional (VI-B) | `config.py` `deploy_be_provider="vercel"`; `deploy/orchestrate.py:1-16,228-262` | Confirmed |
| 52 | Order DB → BE(+URI) → FE(+BE URL) → health-check; dead BE never ships behind live FE (VI-B) | `orchestrate.py:111-207,293-301,390-405` | Confirmed |
| 53 | Pre-flight refuses a localhost DB URI before anything is deployed (VI-B) | `orchestrate.py:116-119`; `db_provision.py:39,107-130` | Confirmed |
| 54 | React Flow topology with per-node status, logs, redeploy (VI-B) | phase-38/62; `@xyflow/react` dependency | Confirmed |
| 55 | Same Playwright suite re-pointed at the deployed URL (VI-C) | `testing/live.py:50-63` (tag filter; untagged suite runs in full; testgen emits no tags) | Confirmed |
| 56 | At most two repair/redeploy/re-validate cycles (VI-C) | `config.py:198-202`; `stages/validate.py:260,347-353` | Confirmed |
| 57 | 65 independently shippable phases in 14 epics (VII) | `plans/README.md` at `c5a7835`: 67 rows (01-67, all ✅), epics 0-13 | **Discrepancy D2** |
| 58 | Table II modules/LOC 175/35 426, 133/18 452, 308/53 878 | `c5a7835` counts (all `backend/app/**/*.py`; frontend/src non-test `.ts/.tsx`) | Confirmed |
| 59 | Table II test-code row 295 files / 46 169 lines | 234 + 61 test files = 295; lines = 36 997 (all 272 `backend/tests/*.py`, i.e. incl. 18 conftest/fake modules and 20 empty `__init__`) + 9 172 | Confirmed (basis note N2) |
| 60 | 10 YAML specs; skeleton ships placeholder FE/BE/E2E suites (Table II) | `eval/specs/*.yaml` = 10; skeleton `*.test.ts(x)`, `e2e/home.spec.ts` | Confirmed |
| 61 | Full-suite run 2 Oct: 2 253 passed; 1 745 BE in 653 s; 508 FE in 167 s; 70 skipped (66 POSIX, 4 opt-in) (VII) | 1 815 collected at `4434e7e` (= 1 745 + 70); FE 508 there; blackbook §5 (lines ~1856-1886, 2026-2029) states the same figures; no run log in the repo | Dated-but-true (timings corroborated only by the blackbook) |
| 62 | >100 runtime settings; admin > env > default; secrets write-only and Fernet-encrypted; key and control-plane URI env-only (VII) | `Settings` has 150 fields; plans/README addition 1; `core/config_admin.py:9`; `config_db.py:21` | Confirmed |
| 63 | Quality gates; single error taxonomy; soft design-quota handling (VII) | `core/errors.py`; `design/resilient.py`; CLAUDE.md | Confirmed |
| 64 | Security posture: per-route authz, no cross-project access, no generated code in control plane (VII) | `docs/threat-model.md`, `docs/hardening-checklist.md`; phase-47 | Confirmed |
| 65 | Six-job CI pipeline (VII) | `ci.yml` jobs backend, config-guard, sandbox, security, frontend, skeleton (same at `c5a7835`) | Confirmed |
| 66 | k3s probe every 0.2 s; 807 requests, zero failures; broken release served none (VII) | `docs/devops/evidence/k3s-rolling-update-verification.txt:33-34`; `docs/devops/README.md:162` | Confirmed |
| 67 | One command `make eval`, run 2 Oct 2026 (VIII-A) | `Makefile:78-79`; bench `started_at` 2026-10-02T10:02Z; apps 12:13-12:58Z | Confirmed |
| 68 | 164 / 257 / 159 problems; BFCL 1 000 AST + 240 irrelevance (VIII-A) | bench JSON `problems`, `categories` | Confirmed |
| 69 | OpenRouter pinned to the developer's upstream, fallback disabled; temp 0, BFCL 0.001 (VIII-A) | `anthropic_client.py:1009-1040`; REPORT.md endpoint string; `bench/bfcl.py:33-34`; bench settings | Confirmed |
| 70 | Repair arm: task + current code + last 1 500 chars, ≤5 attempts, stop after 2 same failure/known code (VIII-A) | `bench/code.py:36-37` and the solve loop (`FEEDBACK_CHARS`, `seen`, `stalls`) | Confirmed |
| 71 | Level 1 does not exercise the diff and import-graph parts; "those are exercised only at Level 2" (VIII-A) | `eval/runner.py:195` uses `RepairLoopController()` with the base analyzer (`stages/repair.py:224`), so there is no import walk. No green run occurred, so there was no last-passing ref or diff. Repair made 0 attempts (DB `repair_report`) | **Discrepancy D3** |
| 72 | Graders 426/426 references pass, 580/580 stubs rejected (VIII-B) | bench JSON `grader_check` (164 + 257 + 5 TS controls) | Confirmed |
| 73 | Each dataset pinned to an upstream commit, verified by SHA-256 (VIII-B) | `bench/datasets.py:29-42`: HumanEval/MBPP/BFCL commit-pinned; HumanEval-TS is pinned by content SHA-256 only ("cannot be pinned to a revision") | **Discrepancy D8** (minor) |
| 74 | Model code runs with no network, all caps dropped, read-only root, mem/CPU/PID limits (VIII-B) | `bench/executor.py:4,63-99` | Confirmed |
| 75 | Eval harness covered by 130 unit tests (VIII-B) | `pytest --collect-only tests/eval` at the 4 Oct tree = 130 | Confirmed |
| 76 | Wilson 95% CIs; exact McNemar (VIII-B) | all Table IV/V CIs reproduce with Wilson z=1.96 | Confirmed |
| 77 | Table IV every cell (problems, rates, CIs, gains, repaired/failed, mean attempts, stall stops, McNemar p, latency, calls, tokens, INR) | recomputed from bench JSON; e.g. pooled tokens 1 160 503, INR 48.15 | Confirmed |
| 78 | Repair converted 13/19 (68%), +1.3 to +2.7 pp; significant only for MBPP p=0.016; +33 calls to 580 (+5.7%) (VIII-C) | recomputed | Confirmed |
| 79 | Iteration budget: 10 at first attempt, 3 at second; six stalled after 2-4 attempts; up to 46 instead of 33 attempts; Fig. 7(b) | recomputed; `paper/figures/fig7-repair-results.py:26` (`rep=[0,10,13,13,13,13]`, `att=[0,19,28,31,33,33]`) | Confirmed |
| 80 | Latency/cost: median 10.6 s (p95 78.4); first-pass 10.4 (56.9); repaired 264.9; unrepaired 894.9 (max 1 424.7); 3.3%; 49%; 29%; reasoning 85%; 110 min, 1 853 calls, INR 64.58 (VIII-C) | recomputed (49.1%, 29.5%, 84.8%, 6 598.9 s) | Confirmed |
| 81 | "With eight problems in flight per suite" (VIII-C) | `bench/code.py:218` concurrency 8; `bench/bfcl.py:397` concurrency 12 | **Discrepancy D9** (minor) |
| 82 | BFCL Table V and text: 92.7% mean; 141 failures = 75 irrelevant calls + 45 (23 wrong count + 22 no match) + 15 invalid values + 6 (4 type, 1 unknown function, 1 no call); median 21.5 s (p95 29.6); irrelevance 68.8% (VIII-D) | bench JSON items, recomputed | Confirmed |
| 83 | Table VI cells (5/7, 7/9; 41/47, 39/44; 0 iterations; 27.2/5.5/1.3 and 36.3/6.9/1.2 min; 34.0 and 44.5 min; 1.38 M/INR 20.85, 1.61 M/INR 24.93) | eval JSON (`stage_seconds` are cumulative) | Confirmed |
| 84 | 75% met, CI [50.5, 89.8]; all 78 unit tests passed, 2 of 13 Playwright (VIII-E) | Wilson(12,16); DB test runs: notes-crud 40 unit + 1/7 E2E, bookmark 38 unit + 1/6 E2E | Confirmed |
| 85 | Zero attempts; escalated `blocked` in ~1.3 s; browser test gives no editable file (VIII-E) | DB `repair_report`: reason `blocked`, wall 1.312 s / 1.24 s; summary "only test files were implicated" | Confirmed |
| 86 | Run stopped after "the first two of the ten specifications" (VIII-E, Abs.) | DB: four specs started 12:13:58Z; the two reported are the first to finish; todo-list (build escalated 12:57) and url-shortener unfinished | Confirmed (nuance N3) |
| 87 | Explainability: 580 stop reasons (561/13/6; no cap, no provider errors); 141 typed BFCL errors; both escalations typed with criterion ids; 4 unmet criteria traced (VIII-F) | bench JSON `stop`, `provider_errors=0`; DB escalation `failing_tests[*].criterion_id` (ac-85ce…, ac-a340…, ac-4260…, ac-da90…) | Confirmed |
| 88 | Failure mode (ii): isolated re-run of a third app's E2E suite, both tests passed in 2.3 s (VIII-F) | no artifact or log found (DB, results, docs) | Unverifiable |
| 89 | Failure mode (iv): eval exposed five harness/platform defects, each fixed with a regression test (VIII-F) | commit `5e4f474` message lists: testgen not wired, detached build not joined, testgen Run not closed, Playwright `workers: 1`, SSE timeout retry, OpenRouter pinning; e.g. `tests/agents/test_openrouter_price_routing.py` | Confirmed (count approximate) |
| 90 | Table III metric definitions (criterion met; delivered; latency on a monotonic clock excluding queueing; cost upper bound, cache ignored) | `eval/runner.py:116-134,404-415`; `bench/code.py:233-246` (timed inside the semaphore); `agents/pricing.py` (no cache rate) | Confirmed |
| 91 | Table VII rows (measured/tested/not-yet) | consistent with rows 3-5, 22, 71, 84-87 | Confirmed |
| 92 | Fig. 5 caption: backend now on Vercel by default, Render optional | D11 amendment (phase-58) | Confirmed |

**Totals:** 92 claims checked.
- Confirmed: 81 (including nuance-only rows 15, 32, 59, 86).
- Dated-but-true: 1 (row 61).
- Unverifiable: 1 (row 88).
- Discrepancy: 9 (rows 2, 21, 27, 36, 43, 57, 71, 73, 81, which are D1-D9).

Literature claims (Sec. I-II, Table I, references) are outside repository verification.

---

## (c) Facts for the Declarations and author contributions

**Funding.** No grant, sponsor or scholarship is mentioned anywhere: docs, plans, README, the
blackbook acknowledgement (which thanks only the guide) or earlier drafts.
`IMPLEMENTATION_PLAN.md` D4 mentions a "₹6k for dev + demo" budget but not its source.
Proposed: "No funding was received for conducting this study." Authors to confirm.

**Competing interests.**
- **Invention disclosure:** "System and Method for Bounded Diff-Aware Self-Healing Software
  Application Delivery", naming all three students and Dr. Payal Kadam. It was "revised in
  response to a six-point intellectual-property review and submitted for filing" (blackbook
  Appendix F; commit `827a158` "Figs & Patent Doc changes" touches an "Invention Disclosure Form
  - ForgeFlow (FILLED).docx").
  - Springer Nature lists planned or pending patents as competing interests.
  - Proposed: "The authors have filed (or: submitted for filing) an invention disclosure titled
    '…' covering the system described in this article. The authors declare no other competing
    interests." Authors to confirm the filing status.
- The project was also submitted to the IBM Bob 2.0 Hackathon (lablab.ai, 25-27 Sep 2026; US
  $12 000 prize pool; status "submitted; event finished"). This is not a competing interest unless
  a prize or other benefit was received.
- No industry collaboration (blackbook App. F: "carried out without an industry mentor or
  industry collaboration").

**Prior submission (must resolve before submitting).** Blackbook Appendix D (2 Oct 2026) says the
paper "has been submitted to an IEEE conference and is under review". Springer Nature does not
allow concurrent submission. The authors must confirm it was withdrawn or rejected, or that this
is the post-decision version. The B.Tech report "may be made electronically available to the
public" (declaration page); mention it in the cover letter if relevant.

**Ethics approval / consent.** Not applicable. There are no human participants or animals; the
draft itself states that the escalation summaries "have not been evaluated with users".

**Use of AI tools (Springer Nature requires documenting LLM use beyond copy editing).**
- The codebase was built with an AI coding agent. `CLAUDE.md` and `STARTUP_PROMPT.md` are
  operating manuals addressed to it.
- 4 commits are authored by "Claude <noreply@anthropic.com>": phases 66-67 and a deploy fix,
  2 Oct 2026, merged via PRs #1/#5/#7 by Sanidhya Awasthi.
- `paper/` contains "Conference Paper Content-humanized.docx".
- LLMs are also a subject of the system and of the evaluation (GLM via OpenRouter).
- Whether and how to add an AI-use statement is the authors' decision. Flagged for the reviewer
  and the user.

**Data availability.**
- The benchmarks are public and the harness pins them (`app/eval/bench/datasets.py:29-42`):
  - HumanEval: openai/human-eval @`463c980b`.
  - MBPP sanitized: google-research/google-research @`f82046ba`, `mbpp/sanitized-mbpp.json`, test
    split = 257.
  - MultiPL-E humaneval-ts: Hugging Face `nuprl/MultiPL-E`, content-hash pinned.
  - BFCL v3: Hugging Face `gorilla-llm/Berkeley-Function-Calling-Leaderboard` @`61fc0608`.
- Recorded outputs:
  - `eval/results/REPORT.md` is tracked.
  - The per-item files `eval/results/bench/bench-1790941973.json` (800 KB) and
    `eval/results/eval-1790946244.json` exist only locally (gitignored). `eval/datasets/` is
    gitignored too.
- Proposed: deposit the two JSON files (e.g. on Zenodo/OSF), or state "available from the
  corresponding author on reasonable request".

**Code availability.**
- `origin` = `git@github.com:Faheem219/ForgeFlow.git`. `gh repo view` returns "Could not resolve
  to a Repository", so the repo is private or inaccessible.
- README "License: Proprietary / coursework project (PBL). Not for redistribution."
- Proposed: "The source code is not publicly available because it is part of a proprietary
  academic project and the subject of an invention disclosure; it is available from the
  corresponding author on reasonable request." Authors to decide.

**Corresponding author.** Not designated in the draft. The first author (repository owner) is the
natural default. Authors to decide.

**Author contributions: evidence.** Git covers 11 Jul to 15 Oct 2026.

| Author | Commits (non-merge) | Lines added in code dirs | Phases in commit subjects |
|---|---|---|---|
| Faheemuddin Sayyed (`Faheem`, faheemuddinsayyed789@gmail.com) | 76 | 71 133 | 01-05, 11-16, 27-28, 36-46, 56-61, 63-64 |
| Sanidhya Awasthi (`Islinger19`, sanidhya.awasthi19@gmail.com) | 51 | 43 246 | 06-10, 17-21, 25-26, 34-35, 47-51, 65 |
| Raghav Sonchhatra (rsonchhatra49@gmail.com) | 31 | 23 231 | 22-24, 29-33, 52, 54-55 |
| Claude agent (merged by Sanidhya) | 3 | 2 666 | 66-67 + deploy fix |
| Dr. Payal Kadam | 0 | — | project guide |

Area notes per author:
- **Faheem:** wrote the eval harness and benchmarks (`5e4f474`) and the Fig. 7 script
  (`ebdf806`), and committed the paper drafts (`08ce512`).
- **Sanidhya:** owns the DevOps reports.
- **Raghav:** owns the repair core (phases 29-32), the paper figures 1-6 drawio (`768cb2b`,
  `ed2b16a`) and the patent-disclosure revision.

The blackbook Table 1.3 ("taken from the repository history … and the review presentations")
matches these lists and adds:
- Faheem: platform CI/CD, Ansible, Kubernetes, monitoring.
- Sanidhya: design clarifying questions, CI fixes.
- Raghav: "patent-disclosure revision and the paper's figures".
- Dr. Kadam: guide; per the acknowledgement, her "questions about what the system could actually
  demonstrate shaped both the design of ForgeFlow and the way we report its results".

**Proposed statement (CRediT-style; confirm roles marked ?).**
- *Faheemuddin Sayyed:* Conceptualization(?), Software (platform foundations, sandbox and IDE,
  test generation and runner, deployment orchestration and live validation, evaluation harness,
  build-reliability and repair-reachability phases, CI/CD), Investigation and Formal analysis
  (ran and analysed the evaluation), Visualization (Fig. 7), Writing – original draft(?).
- *Sanidhya Awasthi:* Software (stage state machine, conductor, artifact model, workspace UI,
  sandbox image, design providers, LLM client and agent tools, requirements capture, credential
  vault and deploy adapters, security hardening, resilience, PWA, layered configuration, offline
  test database), Writing – review & editing(?).
- *Raghav Sonchhatra:* Software (app skeleton, code-generation loop and build stage, failure
  analyser, diff-aware repair agent, bounded loop controller and repair UI, infrastructure
  analyser, build verification and self-heal), Visualization (Figs. 1-6), Writing – review &
  editing(?).
- *Payal Kadam:* Supervision, Methodology(?), Writing – review & editing.
- "All authors read and approved the final manuscript."

---

## (d) Genuine discrepancies, with evidence and a minimal wording fix

Each item is listed so the user can decide. None was applied.

**D1. Test counts (Abstract; Table II; dated "4 October 2026").**
- *Draft:* "2 323 automated tests"; Table II "1 815 tests / 234 files", "508 tests / 61 files",
  total "2 323".
- *Evidence:*
  - At `c5a7835` (4 Oct), with the backend identical to HEAD, `pytest --collect-only` gives
    **1 932** tests in 234 `test_*.py` files.
  - `vitest list` at `c5a7835` gives **518** tests in 61 files. Total **2 450**.
  - 1 815 + 508 = 2 323 is exactly the count at `4434e7e` (2 Oct 11:13 IST, 223 backend test
    files). That is the "full-suite run of 2 October" the next paragraph describes, from before
    phases 66-67 and the eval commit added 117 backend and 10 frontend tests.
  - The LOC and module columns are 4 Oct values, so the table mixes two dates. The "1 815 tests /
    234 files" pairing never existed.
- *Fix A (recommended):*
  - Table II: "1 932 tests / 234 files", "518 tests / 61 files", "**2 450 automated tests**".
  - Abstract: "2 323 automated tests" → "2 450 automated tests".
  - The dated test-performance paragraph stays as is.
- *Fix B:* keep 2 323 and change the Table II row to "1 815 tests / 223 files", adding to the
  caption sentence "…(physical lines of non-test source; test counts from the full-suite run of
  2 October 2026)". This mixes bases in a different way, so it is not preferred.

**D2. Phase count (Sec. VII).**
- *Draft:* "65 independently shippable phases grouped into 14 epics".
- *Evidence:* `plans/README.md` at `c5a7835` lists **67** phases (01-67, all ✅) in epics 0-13.
  Phases 66 (stage reset) and 67 (refine extends plan) were committed 2 Oct 2026 (`66b33b6`,
  `ae86d78`). The blackbook (written 2 Oct) still says 65.
- *Fix:* "65" → "67".

**D3. Which parts of the repair context Level 2 exercised (Sec. VIII-A).**
- *Draft:* "…so Level 1 exercises the bounded controller but not the diff and import-graph parts
  of the context; those are exercised only at Level 2."
- *Evidence:*
  - The harness builds `RepairLoopController()` (`eval/runner.py:195`). Its default analyzer is
    the base `RepairContextAnalyzer` (`stages/repair.py:224`): stack-named files, diff and
    criterion snippets, with **no import-graph walk**. The walk is only in
    `SuiteRepairContextAnalyzer`, which only the Test-stage Repair endpoint uses
    (`testing/repair_router.py:107`).
  - In the recorded run no test run was green before repair, so no last-passing ref existed and
    the diff was empty. The loop made 0 attempts (DB `repair_report`).
  - So neither part was exercised at Level 2.
  - Live validation uses the same base analyzer (`stages/validate.py:243`). That is consistent
    with the draft's limitation that the loop cannot yet act on E2E failures.
- *Fix:* "those are exercised only at Level 2" → "those can be exercised only at Level 2".
- The VIII-E explanation ("the minimal context contained no editable file") matches the stored
  escalation text and needs no change.

**D4. Intent shape (Sec. III-C).**
- *Draft:* "an intent {message, target_stage, action ∈ refine | proceed | skip}".
- *Evidence:* `orchestrator/schemas.py:80-115`. The action set is {refine, proceed, skip,
  unskip, reset}: unskip added 29 Aug, reset added 2 Oct (phase-66). The field is named `stage`
  (plus `payload`).
- *Fix (minimal):* "action ∈ refine | proceed | skip" → "action ∈ refine | proceed | skip |
  unskip | reset". Keep `target_stage` as descriptive notation.

**D5. JUnit reporters (Sec. IV-C).**
- *Draft:* "The output of the JUnit and JSON reporters are normalised…"
- *Evidence:* Only JSON reporters are run and parsed: Vitest, Jest and Playwright
  (`testing/runner.py:47-48,104-114`; `testing/parsers.py:194-210`). There is no JUnit parser.
- *Fix:* delete "JUnit and", giving "The output of the JSON reporters are normalised…".

**D6. Handling of an oversized context (Sec. V-A).**
- *Draft:* "parts that are too large are simply dropped, and the number of times they are dropped
  is recorded".
- *Evidence:*
  - Each file is truncated to its first 8 000 chars, and the diff to 8 000.
  - Only a file that would push the total past 24 000 is dropped.
  - Every truncation and drop is recorded by name and original size in `trimmed`
    (`repair_context.py:237-240,298-306`).
- *Fix:* "parts that are too large are truncated or dropped, and each one is recorded".

**D7. Guard precedence (Sec. V-C), minor.**
- *Draft:* the bullets list regression, stall, cap, then "budget, cancellation and blocked
  attempts … or two consecutive attempts write no file", described as "a deliberate precedence
  order".
- *Evidence:* The code checks regression, then **no-patch** (two empty attempts), then stall;
  the cap applies when the loop is exhausted (`stages/repair.py:379-399`). With the defaults
  (both 2), two empty attempts trip no-patch and stall together, and the code reports
  `no_patch`.
- *Fix (optional):* after "or two consecutive attempts write no file" add "(checked before the
  stall guard)".

**D8. Dataset pinning (Sec. VIII-B), minor.**
- *Draft:* "Each dataset is pinned to an upstream commit and verified by SHA-256 before use".
- *Evidence:* HumanEval, MBPP and BFCL are commit-pinned. MultiPL-E HumanEval-TS "cannot be
  pinned to a revision, so the assembled rows are pinned by content hash instead"
  (`bench/datasets.py:36-42`).
- *Fix:* "pinned to an upstream commit" → "pinned to an upstream commit or content hash".

**D9. Concurrency (Sec. VIII-C), minor.**
- *Draft:* "With eight problems in flight per suite, the four benchmarks took 110 min…"
- *Evidence:* The code suites run 8 at a time (`bench/code.py:218`); BFCL runs 12
  (`bench/bfcl.py:397`).
- *Fix:* "With eight problems in flight per suite" → "With eight problems (twelve BFCL cases) in
  flight per suite".

**Internal-consistency notes.** These are not implementation discrepancies; they are optional and
listed for the user's judgement.
- **N1.** The Abstract says "bounded repair raised pass@1 from 97.0% … to 99.4% …", while
  VIII-A states that the post-repair figure "is not a pass@1 result". Minimal fix: "raised pass@1"
  → "raised the pass rate".
- **N2.** Table II "Test code 295 files / 46 169 lines": the file count covers test files only,
  but the line count also includes 38 support files (conftest, fakes, empty `__init__`). Test
  files alone have 43 496 lines. This is defensible as is.
- **N3.** "the first two of the ten specifications" (Abs., VIII-E): four specs ran concurrently
  in corpus order, and the two reported are the first two to *finish*. The unfinished todo-list
  had already escalated in Build. This is relevant to the "Internal" threat. Optional wording:
  "the first two to complete".
- **N4.** The default routing model id is `claude-haiku-4-5-20251001`. The draft's
  "claude-haiku-4-5" is its alias, which is acceptable.
