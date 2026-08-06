# ForgeFlow — Judge demo prompts (copy-paste)

Short, rehearsed scenarios that show the **bare functionality** end to end: idea → requirements → design
→ build → test → **repair** → deploy → live URL, plus the admin config plane.

Everything below is literal text to type. Pick **A** (the 8-minute headline) and keep **B**/**C** in
reserve. The full click-path, timings and fallbacks live in [`demo-runbook.md`](./demo-runbook.md).

> Before you start: `make dev` up, logged in, Docker running, and `/admin` reachable as an admin user.

---

## A · Todo app — the headline run (~8 min)

The one to show first. Its "Clear completed" action is the thing first-pass generation gets subtly
wrong, so the repair loop visibly earns its keep.

**1 · New project**

```
Hero Demo — Todo
```

**2 · Design → "Describe the UI…"** *(or drag in the todo screenshots instead)*

```
A clean personal todo app. One page: an input to add a task, a list of tasks with a checkbox and a delete button on each, a count of how many are still outstanding, and a "Clear completed" button.
```

**3 · Design → Refine** *(this is the human-in-the-loop beat — one line is enough)*

```
Tighten the spacing and make the outstanding count a badge next to the title.
```

**4 · Requirements** — three features. Type the name, then the acceptance criteria:

| Feature | Acceptance criteria |
|---|---|
| `Manage todos` | `Adding a todo with a title stores it and shows it in the list.`<br>`Adding a todo with an empty title is rejected with a validation error.`<br>`Toggling a todo flips its completed state and persists it across a reload.`<br>`Deleting a todo removes it from the list and from storage.` |
| `Outstanding count` | `The count shows the number of incomplete todos, not the total.` |
| `Clear completed` | `Clear completed removes every completed todo from the list and from storage.`<br>`Clear completed leaves all incomplete todos untouched, even when some are complete.` |

**5 · Build** → watch the code stream into the IDE. *(Say: "it isn't scaffolding React from scratch —
it starts from a fixed skeleton and writes only the feature code.")*

**6 · Test** → the two **Clear completed** criteria go red. That is the setup, not a mishap.

**7 · Repair** → **the money moment.** Say:

> "First-pass generation got the todo basics right but cleared the whole list instead of only the
> completed ones. Watch — it reads that from the failing test and the diff, patches just that, and
> re-runs. And the loop is bounded: a fixed iteration cap, stall detection, then it escalates to a
> human rather than burning tokens forever."

**8 · Deploy** → frontend to Vercel, backend to Render, database provisioned. **9 · Validate** →
Playwright runs against the **live URL**. **10 · Open the URL.** *"Idea in, working app out."*

**Optional non-linearity beat (30 s)** — go *back* to Build and send a change request:

```
Add a filter row with All / Active / Completed above the list.
```

*"No stage is mandatory and you can revisit any of them — downstream work is marked stale, not thrown away."*

---

## B · Notes app — the short run (~5 min)

Use when time is tight or A has already been shown. Simple, but the search path is where a build can
look right and be wrong.

**Project name**

```
Notes
```

**Design prompt**

```
A notes app. A list of notes on the left, an editor on the right with a title and a body, and a search box at the top.
```

**Refine**

```
Show the last-updated time under each note title.
```

**Requirements**

| Feature | Acceptance criteria |
|---|---|
| `Manage notes` | `Creating a note with a title and body stores both and lists it.`<br>`A note without a title is rejected.`<br>`Editing a note's body saves the change and refreshes its updated time.` |
| `Search notes` | `Searching returns notes whose title or body contains the query.`<br>`Search is case-insensitive.`<br>`Clearing the search restores the full list.` |

Then **Build → Test → Repair → Deploy → Validate** exactly as in A.

---

## C · Link shortener — "give it something new" (~5 min)

Keep this one for a judge who asks you to build something unseen. It is not plain CRUD — there is a
redirect route and a uniqueness rule, both structurally easy to get wrong.

**Project name**

```
Short Links
```

**Design prompt**

```
A link shortener. One box to paste a long URL, a button to shorten it, and a table of my links showing the short code, the target URL, and how many times it has been visited.
```

**Requirements**

| Feature | Acceptance criteria |
|---|---|
| `Shorten links` | `Submitting a valid URL returns a short code and stores the pair.`<br>`Submitting a string that is not a URL is rejected with an error.`<br>`Two different URLs never receive the same code.` |
| `Redirect and count` | `Visiting a known short code redirects to the original URL.`<br>`Visiting a code increments that link's visit count by one.`<br>`An unknown short code returns 404.` |

---

## D · Admin config plane (~2 min, no model spend)

Run this while a build is in flight — it costs nothing and answers "is any of this actually
configurable?".

1. Open **`/admin`** → **Models & provider**. Point at the **source badge** on each row:
   `admin` / `env` / `default`. *"Precedence is admin panel > env > code default, and it's visible."*
2. Edit **`MODEL_ROUTING`** → **Save**. The badge flips to **admin**; the next routing call uses it —
   **no restart, no redeploy**.
3. Click **Reset** → the badge falls back to **env**. *"The override is removed, not overwritten."*
4. Type `sandbox` into the search box. *"Every one of the ~110 settings is here — models, budgets,
   sandbox limits, repair thresholds, deploy targets — grouped, searchable, with the env var name
   next to each so the panel and `.env` map onto each other."*
5. Open **`OPENAI_API_KEY`** → **Set**. *"Secrets are write-only: encrypted at rest, decrypted only
   at call time, never shown again. Three bootstrap keys stay env-only and say why."*
6. **The provider swap, if asked:** set `LLM_PROVIDER` → `openai`, `OPENAI_BASE_URL`, and the two
   model ids, then **Save 4**. *"The agents, tools, repair loop and cost accounting are
   provider-agnostic — only the transport changed."*
7. **Audit log** → who changed what, when, before → after.

---

## E · Evidence (~1 min, closing)

- **`/eval`** — the benchmark dashboard: **first-pass vs post-repair** pass rates across the corpus.
  *"The repair loop isn't a demo trick; this is it measured over 10 app specs."*
- The **cost panel** on the project — tokens and ₹ per run, against the budget cap.

---

## If something goes sideways

| Symptom | Do this |
|---|---|
| Design provider errors | Nothing — it auto-falls back (Figma → `fake`) and says so. Call that out as a feature. |
| Repair hits the cap | Expected: it **escalates** with the diff. The bound *is* the contribution — show the escalation. |
| Deploy is slow (> ~3 min) | Switch to the pre-warmed deployment URL from the checklist. |
| Live infra is unavailable | Narrate the headless rehearsal: `cd backend && uv run pytest tests/demo -q -s`. |

Full fallback table: [`demo-runbook.md` §3](./demo-runbook.md).
