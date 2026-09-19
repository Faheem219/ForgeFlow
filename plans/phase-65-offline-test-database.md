# Phase 65 — Offline test database: baked mongod, a skeleton test-DB helper, and network-aware agents

## Title & ID

- **ID:** `phase-65`
- **Title:** Offline test database — a `mongod` baked into the sandbox image, a skeleton test-DB helper, network rules every agent is told, and offline failures recognised instead of re-derived
- **Epic:** Epic 2 — Sandbox & IDE *(added per user directive)*
- **Milestone:** M3 *(hardens M3's stated outcome — tests → a **bounded self-healing repair loop** that can actually go green)*
- **Status:** ✅ done *(2026-09-19)*

## Goal

A generated backend's data tests run against a **real MongoDB inside the offline sandbox** — no download, no network, no shared database — and every agent knows the sandbox's network rules, so a model never again spends its iterations trying to fetch a `mongod` it cannot reach.

## Context

**Reported 2026-09-19 (user):** while diagnosing a failing run, the model (`z-ai/glm-5.3-flash` via OpenRouter) reasoned that it was *"unable to download mongod due to no network access"*. The user asked for either network access or a fix, for this class of problem rather than only this instance.

**Root cause, traced through the code:**

1. **The agent's own test tools have no database.** `run_tests` and `run_command` (`app/agents/tools/definitions.py:111-147`) pass no env and never attach a network, so a DB-touching test falls back to the skeleton's `mongodb://localhost:27017/forgeflow_app` — inside the sandbox that is the sandbox itself, where nothing listens.
2. **The natural fix needs the internet at the wrong moment.** Reaching for `mongodb-memory-server` is what any model does next. It downloads a ~105 MB `mongod` from `fastdl.mongodb.org` on **first use**, and egress windows exist only for package-manager *fetching* subcommands (`needs_registry`, `app/sandbox/network.py`). A test run has none, so the download fails, and the model can only report that it cannot proceed.
3. **The test stage's database is only reachable by accident.** `TestRunner._backend_env` (`app/testing/runner.py:184`) injects a disposable `<app_db>_test` URI on the shared `appdb`, but only the *preview* attaches the network `appdb` lives on (`app/sandbox/preview.py:328`). The URI resolves only if a preview happens to be running.

Nothing in any prompt told the model the network rules, so it rediscovered them by failing.

**Decisions (user-selected, 2026-09-19):**

1. **Bake `mongod` into the sandbox image** and point `mongodb-memory-server` at it through its own env vars. Tests get a hermetic, per-run database with **no network at all**. Rejected: a shared test DB on `appdb` (needs a network attach per test run, and parallel workers share one DB); network for test runs (untrusted code with internet, Golden Rule 3); an allowlisted egress proxy (new infrastructure, and allowlisted hosts are still reachable by untrusted code).
2. **The skeleton ships the helper and its devDependency**, already in the lockfile, so the model imports `useTestDb()` and never installs or improvises a harness. This matters most for fast, cheap models.
3. **Every agent is told the network rules.** The user deferred the other safeguards to this phase's judgement. Chosen: **deterministic detection** of offline failures, where an unrepairable environment finding stops the repair loop at zero iterations and a repairable one hands the agent a precise hint; and **one DB path everywhere by construction** (image env + Jest `globalSetup`), rather than attaching networks to test runs.
4. **Real DB for data criteria.** Generated tests for models, persistence and queries run against the real (in-memory) database. Only pure logic may skip it.

**Locked decisions honoured.**
- **No network is relaxed.** The sandbox gains a binary, not a route (D5 / Golden Rule 3). `mongod` binds `127.0.0.1` inside the container, and the image env makes a download *impossible* rather than merely unlikely (`MONGOMS_RUNTIME_DOWNLOAD=false`).
- **The generated-app stack is unchanged (D2).** MongoDB is already the stack's database; `mongodb-memory-server-core` is a Jest test devDependency, not a new stack.
- **Isolation is stronger (D8).** Tests no longer touch `appdb` at all, not even the `_test` sibling, so a suite that clears collections cannot reach any project's data.
- **The repair loop only gets shorter (D7 / Golden Rule 5).** It gains one terminal reason, `environment`, and no longer leash.
- **Cost discipline (Golden Rule 7).** A cheap path no longer means an unproven one, because data tests hit a real database instead of a mock. And the loop stops paying `MODEL_CODEGEN` tokens for a failure no patch can fix.
- **No new hard prerequisite (D12).**
- **The skeleton is still copied deterministically, never generated (phase-22).** Phase-54 chose not to edit the template for *that* phase. This phase edits it deliberately, as selected.

## Depends on

- **Phase IDs:** `phase-10` (sandbox image), `phase-11` (sandbox manager — staleness by image id), `phase-22` (skeleton), `phase-27` (testgen prompts), `phase-28` (test runner), `phase-29`–`phase-31` (repair context, agent, loop), `phase-47` (sandbox hardening posture). All ✅.
- **External:** network at **image build** time only (fastdl.mongodb.org); Docker to build/smoke the image.

### Prerequisite check

- `sandbox/Dockerfile` builds today (`make sandbox-build`).
- `templates/app-skeleton` installs and its unit suites pass (`make skeleton-verify`).
- `uv run pytest -q` is green on `main`.

## Detailed tasks

### A. Sandbox image — a baked `mongod`

1. **`sandbox/Dockerfile`**:
   - `ARG MONGO_VERSION=8.0.32` plus `MONGO_SHA256_AMD64` / `MONGO_SHA256_ARM64`, pinned **beside the `mongod` layer** rather than at the top with the Playwright/pnpm pins: a changed ARG misses the cache on every later `RUN`, so this way a version bump rebuilds only this layer, not apt/pnpm/Playwright. 8.0 matches Atlas's default major version, so the tests and the deployed database run the same server line.
   - One `RUN` layer picks the official **community** tarball by `TARGETARCH`, falling back to `dpkg --print-architecture` for the legacy builder: `mongodb-linux-x86_64-debian12-<v>` for amd64 and `mongodb-linux-aarch64-ubuntu2204-<v>` for arm64. MongoDB publishes no arm64 Debian build; the jammy build needs glibc ≥ 2.35 and bookworm ships 2.36. The layer verifies MongoDB's published **sha256**, installs **only** `bin/mongod` to `/usr/local/bin/mongod` (0755), and deletes the tarball.
   - After that layer, `ENV MONGOMS_SYSTEM_BINARY=/usr/local/bin/mongod`, `MONGOMS_VERSION=${MONGO_VERSION}` (no version-conflict warning, and wiredTiger is chosen for ≥ 7.0), `MONGOMS_RUNTIME_DOWNLOAD=false` (fail fast, never dial out), and `MONGOMS_DISABLE_POSTINSTALL=1` (the non-core package's install hook never fetches 105 MB inside an install window).
   - Build-time regression guard: `mongod --version` as root **and** as `app`, alongside the existing `pnpm --version` guard.
2. **`sandbox/smoke/run_smoke.sh`**: two new steps under the real runtime posture (read-only root, `--network none`, `--cap-drop ALL`, `no-new-privileges`, `/tmp` tmpfs, pids + memory caps):
   - `mongod` starts and accepts a TCP connection on loopback.
   - **End to end:** `mongodb-memory-server-core` is installed *with* network into a volume (as an install window would), then run with **none**. It must start the baked binary and complete an insert/find (`smoke/mongo-check.cjs`). This is the regression test for the reported failure.
3. **`sandbox/README.md`**: document the baked `mongod`, the four `MONGOMS_*` variables, and why none of them opens a route.

### B. Skeleton — the test-DB helper (`templates/app-skeleton/backend`)

4. **`package.json`**: devDependency `mongodb-memory-server-core` `^11.2.0`. The `-core` package has no postinstall, so a developer machine, CI, or the Vercel build of the deployed backend never downloads 105 MB at install. The sandbox never downloads at all.
5. **`pnpm-lock.yaml`**: regenerated (`pnpm install --lockfile-only`) so the dependency is resolved offline from the store after the first install.
6. **`src/test/globalSetup.ts` / `src/test/globalTeardown.ts`**: one in-memory `mongod` per Jest run (`--wiredTigerCacheSizeGB 0.25`, sized for the 1 GB sandbox). It exports the URI as `FORGEFLOW_TEST_MONGODB_URI` **and** `MONGODB_URI`, so the agent's `run_tests`, a raw `pnpm test` and the Test stage all reach the same server, and any test that reads `config.mongoUri` stays in memory. **Fail soft:** if the server cannot start, it records `FORGEFLOW_TEST_DB_ERROR` and the suites that don't need a database still run.
7. **`src/test/db.ts`**: `useTestDb()` (one call per file registers the connect, clear-between-tests and drop-afterwards hooks), plus `connectTestDb` / `clearTestDb` / `disconnectTestDb`. Each file gets its own database (`test_<worker>_<random>`). If the server is unavailable, it throws an error carrying the stable marker `[forgeflow:test-db-unavailable]` and the recorded reason, instead of hanging on mongoose's 10 s buffering timeout.
8. **`src/test/db.test.ts`**: the skeleton's own proof. It covers a Mongoose round-trip, empty collections at the start of each test, and a per-file database name. It is also a canary: on a stale image it fails with the marker, and the loop escalates as `environment` (task 13).
9. **`jest.config.js`**: `globalSetup` / `globalTeardown`, and `maxWorkers: 2`. Jest sizes its pool from the *host's* cores, and the sandbox has 1 CPU and 1 GB now shared with a resident `mongod`.
10. **`tsconfig.build.json`**: exclude `src/test/**`, so a production build never compiles test tooling that imports a devDependency.
11. **`README.md`**: the helper, where it lives, and the rule that it is scaffold.

### C. Agents — rules, detection, and an environment stop

12. **`app/sandbox/offline.py`** (new, pure): `diagnose_offline(text) -> OfflineDiagnosis | None`, table-driven and case-insensitive, most specific first:
    - **`test_db_unavailable`**, *not repairable*: the helper marker, `fastdl.mongodb.org`, `could not find an valid binary path`, `no binary at path`, or `MongoBinaryDownload`. The hint says to rebuild the sandbox image and that no code change fixes it.
    - **`runtime_download`**, *repairable*: Puppeteer's `Could not find Chrome/Chromium`. The hint says to use Playwright, whose Chromium is baked in.
    - **`external_host`**, *repairable*: `getaddrinfo|querySrv ENOTFOUND|EAI_AGAIN <dotted host>`, where the host is named in the hint. The hint says to mock the client or inject it, and use `useTestDb()` for data. Dot-less names (docker services such as `forgeflow-appdb`) and loopback are **excluded**; the existing `appdb` diagnostics own those.
13. **`app/agents/repair_context.py`**: `RepairContext.sandbox_hints: list[str]` (repairable) and `RepairContext.environment: OfflineDiagnosis | None` (not repairable), filled by scanning each failing test's message/assertion/stack. Both go into `to_dict()` for the audit trail.
14. **`app/agents/prompts/repair.py`**: render `## Sandbox environment` with the hints when there are any. It is absent otherwise, so existing prompts are byte-identical.
15. **`app/orchestrator/stages/repair.py`**: `REASON_ENVIRONMENT = "environment"`. When the analyzed context carries `environment`, escalate **before** calling the agent: zero patch attempts, zero `MODEL_CODEGEN` tokens. The summary carries the hint and ends with "fix the environment, then run the tests again" instead of inviting code guidance.
16. **`app/agents/prompts/__init__.py`**: `BASE_SYSTEM` gains the network rules. There is no internet while code runs, builds or tests; only package installs briefly reach the registry; nothing may download at run time; external services are mocked in tests. It also lists what is already inside (pnpm, Playwright's Chromium, a MongoDB binary that `mongodb-memory-server` picks up automatically). `PROMPT_VERSION` is bumped.
17. **`app/agents/prompts/testgen.py`**: data-touching backend criteria **must** use `useTestDb()`: no Mongoose mocks for them, no direct `mongodb-memory-server`, and no `MONGODB_URI`. Only pure logic skips the database. No test may call the internet. One fallback sentence covers workspaces instantiated before this phase: there, mock as before. The test author has no `install_deps`, so it cannot add a database library itself, and must not try to.
18. **`app/agents/prompts/codegen.py`**: `backend/src/test/` joins the do-not-rewrite scaffold list, with one line on `useTestDb()`.
19. **`app/testing/runner.py`**: docstring only. The injected `<app_db>_test` URI is now superseded by the Jest `globalSetup`, and survives as the path for pre-phase-65 workspaces. No behaviour change.
20. **`frontend/src/features/testing/Escalation.tsx`**: a label for the `environment` reason.

### D. Docs

21. `CLAUDE.md` and `IMPLEMENTATION_PLAN.md` (§ tech stack): the sandbox image line gains the test-only `mongod`. `DEVELOPER.md` troubleshooting gains the stale-image row. `plans/README.md` gains the status row, a changelog entry and "Additions beyond the roadmap" item 10.

## Out of scope for this phase

- **Retrofitting existing workspaces** with the helper files. A rebuilt image already fixes their reported failure: `mongodb-memory-server` now finds the baked binary. For new DB tests there, the testgen fallback keeps the old mock-based convention. Copying scaffold into a live workspace, patching its Jest config and installing a dependency is its own decision.
- **Attaching `appdb` to test runs** (bug 3's literal fix). Tests no longer need it, and doing it would give generated test code a route to the server holding every project's data.
- **A general egress allowlist/proxy.** It was considered and rejected by the user for this phase.
- **Build verification** (phase-55) never runs the test database, so `classify_output` is unchanged.
- **Playwright version drift** (`Executable doesn't exist at /ms-playwright/...`). It's a different mechanism, and the browser is already baked.

## Acceptance criteria

1. The sandbox image contains `/usr/local/bin/mongod` (MongoDB 8.0.32), runnable as `app`. The build fails if the checksum does not match or the binary cannot start.
2. Under the real posture (read-only root, **no network**, dropped caps, tmpfs `/tmp`, pids/memory caps), `mongodb-memory-server-core` starts the baked binary and completes an insert/find. `MONGOMS_RUNTIME_DOWNLOAD=false` is set, so no code path can dial out for a binary.
3. A freshly instantiated skeleton's backend suite includes a real-database test that passes. Its database is unique per test file and empty at the start of each test.
4. If the in-memory server cannot start, tests that don't use the database still pass. Those that do fail fast with `[forgeflow:test-db-unavailable]` and the reason, not after a buffering timeout.
5. A failing test run whose failures carry a `test_db_unavailable` signal escalates with reason `environment` **before any patch attempt** (the agent is never called), and the summary names the fix.
6. A failing test run with a repairable offline signal (an external host) reaches the agent with a `## Sandbox environment` section naming the host. Without any signal the repair prompt is unchanged.
7. Every agent's system prompt states the network rules. The testgen prompt requires `useTestDb()` for data criteria and forbids direct `mongodb-memory-server` / `MONGODB_URI`.
8. `backend/src/test/*` is classified as scaffold (`skeleton_relpaths()`), is never counted as feature code by the phase-64 structural check, and is excluded from the production build.
9. `uv run pytest`, ruff, black and mypy are clean. The frontend tests pass. `make skeleton-verify` passes. The sandbox smoke passes.

## Tests to write

- **`backend/tests/sandbox/test_offline_diagnosis.py`**: each signal class, with repairable-ness and hint; external host named, and dot-less/loopback hosts excluded; `querySrv` SRV prefix stripped; case-insensitive; precedence (a download failure that also mentions a host reads as `test_db_unavailable`); clean output gives `None`.
- **`backend/tests/sandbox/test_sandbox_image_mongod.py`**: static checks on the real `sandbox/Dockerfile`: pinned version shared by the tarball and `MONGOMS_VERSION`, sha256 verified for both architectures, the four `MONGOMS_*` values, and the `mongod --version` build guard.
- **`backend/tests/agents/test_skeleton_test_db.py`** against the real template: the `-core` devDependency (and **not** the postinstall-downloading package) in both `package.json` and the lockfile; Jest `globalSetup`/`globalTeardown` pointing at files that exist; `maxWorkers` bounded; `src/test` excluded from the build; the helper exporting `useTestDb` with the marker; scaffold-not-feature classification.
- **`backend/tests/agents/test_repair_context_offline.py`**: the analyzer fills `environment` / `sandbox_hints` from failing-test output (one hint per cause, not per test), leaves both empty for ordinary failures, and serialises them. The prompt's `## Sandbox environment` section renders only when hints exist, sits right after the failures, and leaves the rest of the prompt byte-identical.
- **`backend/tests/orchestrator/stages/test_repair_environment_stop.py`**: `environment` escalates at zero iterations with the agent never called and the stage `awaiting_user`; a repairable hint does **not** stop the loop.
- **`backend/tests/agents/test_prompts_network_rules.py`**: the base prompt carries the rules, the testgen prompt the DB policy, and the codegen prompt the scaffold line.
- **Frontend:** `Escalation.test.tsx`: the `environment` label renders.
- **Generated-app fixture:** `templates/app-skeleton/backend/src/test/db.test.ts` (run by `make skeleton-test`).

## Manual verification steps

```bash
make sandbox-build && make sandbox-smoke          # mongod + offline mongodb-memory-server steps pass
docker run --rm forgeflow-sandbox:latest bash -c 'mongod --version | head -1; env | grep ^MONGOMS_'
make skeleton-verify                              # includes src/test/db.test.ts (real DB)
cd backend && uv run pytest -q && uv run ruff check . && uv run black --check . && uv run mypy .
cd frontend && pnpm test
```

In the app: create a project → Build → Test. The backend report includes `useTestDb` tests passing with no preview running. `docker exec forgeflow-sb-<id> printenv MONGOMS_SYSTEM_BINARY` prints `/usr/local/bin/mongod`.

## Rollback / cleanup notes

Revert the commit and `make sandbox-build`. Sandboxes are recreated from the older image on next use (staleness is by image id). Workspaces instantiated while this phase was live keep the helper and its test; on the old image that test fails with the marker and the loop escalates as `environment`, which says exactly what happened. Nothing persistent is migrated.

## Design notes

- **Why a tarball and not `COPY --from=mongo:8.0`.** The official `mongo:8.0` image is built on Ubuntu noble (glibc 2.39), and its binary is not guaranteed to load on bookworm (2.36). The tarballs are MongoDB's own builds for exactly this target, carry a published checksum, and are what `mongodb-memory-server` itself would have downloaded.
- **Why one server per run (`globalSetup`) rather than one per file.** Every `mongod` costs ~30 threads against `SANDBOX_PIDS_LIMIT=256`, and memory against 1 GB. Per-file *databases* on one server keep the isolation at a fraction of the cost.
- **Why `MONGODB_URI` is overridden in tests.** It makes "the database a test sees" a property of the test harness rather than of whichever process launched Jest. That is what makes the agent's `run_tests` and the Test stage agree by construction, which is the "same DB env everywhere" safeguard without any network attach.
- **Why `test_db_unavailable` is not repairable.** Nothing in `backend/src` can supply a binary that isn't in the image. Treating it as code is the exact loop the user reported: a model re-deriving, iteration after iteration, that it has no network.

## Status

✅ done *(2026-09-19)*

**As built / verified:**
- **Image:** builds on amd64 (Docker Desktop, BuildKit). `mongod` 8.0.32 runs as `app`. The binary is **223 MB** on disk, more than the ~120 MB estimated when the approach was chosen; it is the only addition.
- **Smoke:** `sandbox/smoke/run_smoke.sh` passes end to end, including both new steps. `mongod` accepts connections, and `mongodb-memory-server-core` round-trips a document, under `--network none --read-only --cap-drop ALL --pids-limit 256 --memory 1g`.
- **Offline, in the sandbox image:** the skeleton, installed with network and then run with `--network none --read-only --cpus 1 --memory 1g --pids-limit 256`, passes **11/11** Jest tests, including `src/test/db.test.ts` against the baked binary.
- **On a host:** `typecheck`, `lint`, `build` (no `test/` in `backend/dist`) and `test:unit` pass. With no binary and downloads disabled, the non-DB suites still pass and the DB suite fails at once with the marker (3.5 s for the whole run; 15.7 s before `clearTestDb` learned to skip a connection that never opened).
- **Control plane:** 46 new tests pass; ruff and black clean. `mypy` reports 7 errors, all in test files this phase does not touch (`test_openai_transport.py`, `test_codegen_phase_evidence.py`, `test_design_questions.py`, `test_stitch_mcp_transport.py`). **Frontend:** 508/508 Vitest, ESLint and `tsc` clean.
- **Found on the way, fixed:** the smoke's pnpm-posture step had been failing with `EROFS` because `pnpm store path` probes its working directory, and the step left `/workspace` on the read-only root. It now mounts a workspace volume, as the manager always does.
- **Full backend suite:** 1733 passed, 69 skipped, 2 failed. Both failures reproduce identically on the parent commit, so they are pre-existing and not fixed here. `test_conductor_legality.py::test_deploy_proceed_allowed_after_build_complete` reaches a real Docker daemon, whether or not one is running. `test_sandbox_isolation.py::test_docker_inspect_confirms_the_flags_on_a_real_container` needs a local `forgeflow-sandbox:latest`. Pointed at the phase-65 image (`SANDBOX_IMAGE=…`), the whole isolation audit passes **15/15**: the baked `mongod` leaves the container's flags (no network, dropped caps, read-only root, limits) exactly as they were.
- **Not verified here:** the arm64 build (checksum and tarball name were checked against MongoDB's `current.json`; the image has not been built on arm64).
