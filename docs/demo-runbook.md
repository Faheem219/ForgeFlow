# ForgeFlow — Hero Demo Runbook

The demo proves the thesis (D1): a non-linear, human-in-the-loop pipeline where a **bounded,
diff-aware repair loop** turns a build that *almost* works into one that does. Everything below is
engineered so that money moment happens on purpose, not by luck.

- **Scenario:** [`eval/specs/hero-todo.yaml`](../eval/specs/hero-todo.yaml) — a todo app with a
  "Clear completed" bulk action that first-pass generation tends to get subtly wrong.
- **Copy-paste prompts** for this and two shorter scenarios (plus the admin-panel walkthrough):
  [`judge-demo-prompts.md`](./judge-demo-prompts.md).
- **Regression guard:** `backend/tests/demo/test_hero_smoke.py` runs the whole path headlessly (costly
  legs mocked) and asserts every milestone. Run it before every rehearsal.
- **Reset to clean state:** `uv run python -m scripts.demo reset` (see §4).

---

## 1. The path (what the audience sees)

| # | Stage | Action | The point | ~time |
|---|-------|--------|-----------|-------|
| 1 | **Design** | Drag in the todo screenshots → *Generate* | Screenshots → a real design | 30–60 s |
| 2 | **Design** | Type a refine ("tighten the spacing, make the count a badge") → *Generate* | **Non-linearity + human-in-the-loop**: you steer it | 30–60 s |
| 3 | **Requirements** | Review the pre-captured spec (or capture live) → *Proceed* | Structured spec → deterministic tests | 15–30 s |
| 4 | **Build** | *Build* | Generates **onto the skeleton** — live code streams in the IDE | 1–3 min |
| 5 | **Test** | *Run tests* | First pass **fails** the two "Clear completed" criteria — the setup | 30–60 s |
| 6 | **Test → Repair** | *Repair* | **The money moment**: the loop reads the diff, patches, re-tests → green. Watch the iteration count | 1–3 min |
| 7 | **Deploy** | *Deploy* (seamless) | FE→Vercel, BE→Render, DB→Atlas — dual-mode | 1–3 min |
| 8 | **Validate** | *Validate* | Playwright against the **live URL** → green | 30–90 s |
| 9 | — | Open the live URL | **Idea in, working app out** | — |

**The line to say at step 6:** *"First-pass generation got the todo basics right but cleared the whole
list instead of only the completed ones. Watch — it finds that from the failing test, patches just
that, and re-runs."* That is the contribution, in one sentence.

If you pre-seeded (§4), steps 3–4 start from a project that already has the requirements, so you can
jump straight to Build.

---

## 2. Pre-demo checklist (run T-30 minutes)

- [ ] `git pull` and the app is on the intended build (version shows in the sidebar footer).
- [ ] `make dev` up: API + web + Mongo + proxy healthy (`/health` returns ok).
- [ ] **Rehearsal smoke passes:** `cd backend && uv run pytest tests/demo -q` → all green.
- [ ] `ANTHROPIC_API_KEY` set and has budget headroom (check `/admin/cost` or a project's cost panel).
- [ ] Design provider reachable **or** the fallback is set (`DESIGN_FALLBACK_CHAIN=figma,fake`).
- [ ] Deploy creds present (`VERCEL_TOKEN`, `RENDER_API_KEY`) **or** you will demo the mocked/pre-warmed path (§3).
- [ ] Docker/Colima up; no stale sandboxes: `make sandbox-reap-all`.
- [ ] Clean state: `cd backend && uv run python -m scripts.demo reset && uv run python -m scripts.demo seed`.
- [ ] Log in as the demo user; the "Hero Demo" project is present with requirements captured.
- [ ] Screenshots for step 1 are on the desktop, ready to drag.
- [ ] A **pre-warmed deployment** exists as the deploy fallback (deploy the hero once ahead of time; keep the URL).

---

## 3. Reliability fallbacks (if X fails, do Y)

Every risky stage has a rehearsed answer. The theme: **degradation is a feature here, not a failure** —
say so out loud.

| Risk | Symptom | Fallback (do this) | Why it's fine |
|------|---------|--------------------|---------------|
| **Stitch quota/auth** | Design generate errors | Nothing — it **auto-falls-back** to Figma, then `fake` (phase-48). The assistant message says so. | "It noticed the provider was down and switched — the pipeline degrades gracefully." |
| Design still stuck | Both providers down | Set `DESIGN_PROVIDER=fake` (or per-project) → deterministic canned design | The demo is about the *pipeline*, not the artwork |
| **Model hiccup** | A stage stalls or a call 429s | Wait — the agent client retries with backoff; the repair loop escalates to a human if it stalls | "Bounded and honest: it retries, then asks for help rather than looping forever" (D1, Golden Rule 5) |
| **Repair doesn't converge** | Iterations hit the cap | Expected & safe — it **escalates** with the diff and the remaining failures | The bound *is* the contribution; show the escalation UI |
| **Live deploy flaky** | Deploy errors or is slow | Switch to the **pre-warmed deployment** URL you prepared, or run deploy in the rehearsed mocked path | The deployed artifact is identical; you're only swapping *when* it was deployed |
| **Live validation flaky** | Playwright can't reach the URL | Re-run validate once; if still red, open the live URL by hand to show it works | Network flakiness ≠ a broken build |
| **Sandbox won't start** | Build/test can't run | `make sandbox-reap-all`, then re-ensure the project's sandbox | Recovers from the workspace volume (phase-48) |
| **Everything is on fire** | Live infra unavailable | Run the **headless rehearsal** (`uv run pytest tests/demo -q -s`) and narrate it | It exercises the real pipeline logic; only the costly legs are mocked |

Decision points, stated plainly: choose the pre-warmed deploy the moment live deploy exceeds ~3 min;
switch the design provider the moment Stitch errors twice; never let the audience watch a spinner for
more than ~15 seconds without narrating what it's doing.

---

## 4. Seed & reset

```bash
cd backend
uv run python -m scripts.demo seed     # create the demo account + a Hero Demo project (needs DEMO_PASSWORD)
uv run python -m scripts.demo status    # what's currently seeded
uv run python -m scripts.demo reset     # delete the demo user's projects; keep the account
```

`seed` pre-captures the hero requirements and marks them complete, so a run starts at design/build.
`reset` cascades each project's stage state, conversation and artifacts, and best-effort reaps its
sandbox and app database — so the next rehearsal starts clean. The cycle is repeatable.

Set the credential first (never a shipped default): `export DEMO_PASSWORD=…` (or put it in `.env`).

**After a live rehearsal**, also clean up provider-side resources you created (Vercel/Render
deployments, Atlas DBs) so they don't accrue — `reset` handles *our* DB records, not a third party's
dashboard.

---

## 5. The rehearsal smoke, in words

`tests/demo/test_hero_smoke.py` drives `eval/specs/hero-todo.yaml` through the real
`EvalRunner`/conductor with the model, sandbox and live deploy injected as fakes, and asserts:

1. the **skeleton is instantiated** (the build leg drives the build handler),
2. a **genuine first-pass failure** (2 of 7 criteria red — the Clear-completed pair),
3. the **repair loop fixes it** (`outcome == "fixed"`, post-repair green, a positive repair delta),
4. the **deploy is healthy** with a live URL and a screenshot→URL time,
5. **live validation runs**, and the outcome is `delivered`, with per-run resources torn down.

If that test is green, the hero path's *logic* is intact; the pre-demo checklist then covers the live
infrastructure the test deliberately mocks.
