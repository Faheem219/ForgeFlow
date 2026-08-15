# Phase 57 — Provider-agnostic request compatibility

## Title & ID

- **ID:** `phase-57`
- **Title:** Provider-agnostic request compatibility (param adaptation & capability memo)
- **Epic:** Epic 12 — Admin & Config *(extends `phase-53`'s OpenAI-compatible provider)*
- **Milestone:** M4 *(hardening — the platform must work against whatever endpoint the operator points it at)*
- **Status:** ✅ done *(2026-08-15)*

## Goal

Stop hard-coding one provider's request schema. When an OpenAI-compatible endpoint rejects a
parameter, the transport must **repair the request and continue** — renaming what has been renamed,
dropping what is unsupported, reshaping messages the model wants differently, and falling back to a
non-streamed call when streaming is refused — then **remember** the working shape so the cost is
paid once, ever.

## Context

The **reported failure** — `gpt-5.4-mini` over the official OpenAI endpoint:

```
400 - {'error': {'message': "Unsupported parameter: 'max_tokens' is not supported with this
model. Use 'max_completion_tokens' instead.", 'type': 'invalid_request_error',
'param': 'max_tokens', 'code': 'unsupported_parameter'}}
```

`build_openai_params` (`app/agents/anthropic_client.py:622-640`) always sends `max_tokens`, because
that is the field name on `MessageRequest` — which is **Anthropic-shaped by construction** (`:53-59`).
Every agent surface (codegen, repair, testgen, planning) goes through this one function, so on such
a model *every* call fails and the whole product is unusable. The user's directive is explicit:
*"kindly ensure that these kind of errors dont occur in the future not just for `max_tokens` param
but for other params as well, please find out a way to make this llm provider agnostic and not
coded according to only one llm provider."*

The codebase already contains **exactly one** instance of the right idea, hard-coded for a single
parameter: `_rejects_stream_options` (`:642-666`) plus the `_NO_STREAM_USAGE` memo (`:620`) detect
Mistral/Azure refusing `stream_options`, drop it, remember the endpoint, and retry
(`:713-722`). It works — and it is a one-off: a second incompatible parameter needs a second
bespoke predicate, a second memo and a second branch in the retry loop. This phase **generalises
that single case into the mechanism**, and the `stream_options` special-case collapses into it.

That the failure is a **400, not a 5xx**, is why the existing retry ladder cannot help:
`_is_transient_openai_error` (`:583-597`) deliberately treats 400 as fatal, and correctly so — a
malformed request retried unchanged is malformed again. The insight this phase turns into code is
that *some* 400s are **self-describing**: the provider names the offending parameter and often the
replacement. Those are repairable.

Locked decisions honoured: no new provider or SDK (this is `phase-53`'s transport, made robust);
the `Transport` protocol and `MessageRequest` are unchanged, so **`SdkTransport` (Anthropic) is not
touched at all** and agents/tools/cost code stay provider-agnostic (the phase-20 seam); secrets are
untouched (§7); every loop stays bounded (D7 / Golden Rule 5) — adaptation adds a *finite* repair
budget, not an open-ended retry.

### User decisions (asked and answered, 2026-08-15)

1. **Mechanism — seed table *and* reactive repair.** The table avoids a wasted first call on
   well-known families; the reactive half is what makes it genuinely provider-agnostic, because it
   works for models that do not exist yet.
2. **Persistence — the phase-51 settings store**, so a learned quirk survives a restart and is
   visible (and clearable) in the admin panel.
3. **Coverage — all four classes:** param rename, unsupported-param drop, message-shape
   (`system` → `developer`), and streaming-unsupported → non-streaming fallback.
4. **No admin editor this phase.** Backend + config only.

## Depends on

- **Phase IDs:** `phase-53` (the OpenAI transport this hardens), `phase-51` (the layered config
  service + `PlatformSetting` store used for the memo), `phase-20` (the `Transport` seam),
  `phase-46` (cost accounting — the usage trailer this must not silently lose).
- **External:** the `openai` SDK (already optional-installed; every unit test uses
  `pytest.importorskip` as `tests/agents/test_openai_strict_endpoints.py:24` does). **No network
  and no API key is needed for any test** — the adapter is pure and the memo is injectable.

### Prerequisite check

- `cd backend && uv run pytest -q tests/agents/test_openai_transport.py tests/agents/test_openai_strict_endpoints.py` green.
- `tests/config/test_registry_complete.py` green **before** starting — it fails the build if a
  `Settings` field has no registry entry, and this phase adds three.

## Detailed tasks

Ship as **two commits**: tasks 1–5 are the adapter + memo (pure, fully testable, no behaviour change
until wired), task 6–9 wire it into the transport and retire the `stream_options` special case.

### Commit 1 — the adapter

1. **`app/agents/provider_compat.py` (new).** The whole mechanism, pure and I/O-free.

   ```python
   class Adaptation(StrEnum):
       rename = "rename"; drop = "drop"; developer_role = "developer_role"; no_stream = "no_stream"

   @dataclass(frozen=True)
   class Repair:
       adaptation: Adaptation
       param: str                      # the offending parameter, as the provider named it
       replacement: str | None = None  # for `rename`
       reason: str = ""                # the provider's own words, for the log + the memo

   def diagnose_param_error(exc: BaseException) -> Repair | None
   def apply_repairs(params: dict, repairs: Iterable[Repair]) -> dict
   def seed_repairs(model: str) -> list[Repair]
   ```

   - **`diagnose_param_error`** is the reactive half and the load-bearing piece. It reads the
     structured error **first** and the message text only as a fallback, because OpenAI's own error
     body already carries `param` and `code`:
     - `body["error"]["param"]` names the parameter; `code == "unsupported_parameter"` /
       `"unknown_parameter"` / `"unsupported_value"` classifies it.
     - A **replacement** is extracted from the message with one narrow pattern —
       `Use '<name>' instead` — which is the phrasing OpenAI uses. Found ⇒ `rename`; not found ⇒
       `drop`.
     - Text fallback for endpoints that do not populate `param`: match the parameter name out of
       the message against **the keys actually in the request**, never a hard-coded list — a name we
       did not send is not ours to repair.
     - Returns `None` for any 400 it cannot attribute to a parameter. **That is the safety
       property**: an unattributable 400 falls through to today's fatal path rather than triggering
       a blind retry.
   - **`apply_repairs`** is the only writer of the params dict. `rename` moves the value under the
     new key (so an output cap stays a cap — dropping `max_tokens` would silently uncap spend, which
     matters for D13 budget accounting); `drop` removes it; `developer_role` rewrites the leading
     `system` message's role; `no_stream` sets `stream=False` and removes `stream_options`.
   - **`seed_repairs`** is the table half, deliberately **tiny** and pattern-matched on the model id:
     the reasoning families (`o1`/`o3`/`o4`, `gpt-5*`) get
     `max_tokens → max_completion_tokens`, `drop temperature`, `drop top_p`, `developer_role`. It is
     a *starting guess*, never a gate — a wrong seed is corrected by the reactive half on the next
     call, which is why the table lagging behind new models can no longer break anything.

2. **`ParamMemo` (`app/agents/provider_compat.py`).** The learned shape, keyed by
   `(endpoint, model)` — **not** by endpoint alone, because `max_tokens` is fine on `gpt-4o` and
   fatal on `gpt-5*` **at the same base URL**. Keying the existing `_NO_STREAM_USAGE` memo by
   endpoint only was safe for `stream_options` (an endpoint-wide schema fact) and is *not* safe
   here; the wider key subsumes it.

   ```python
   class ParamMemo:
       def get(self, endpoint: str, model: str) -> list[Repair]
       def learn(self, endpoint: str, model: str, repair: Repair) -> None
       def snapshot(self) -> dict[str, list[dict]]     # JSON for persistence
       def load(self, data: dict) -> None
   ```

   In-process and synchronous, mirroring `_NO_STREAM_USAGE`, so the hot path never awaits.

3. **Persistence via the settings store (`llm_param_quirks`).** A single **json**-typed config key
   holding `{"<endpoint>|<model>": [<repair>, …]}` — the same shape and mechanism as the existing
   `model_pricing_json` key (`config.py:109`, `config_registry.py:378-385`), so no new collection,
   no new admin surface, and it is already visible and clearable from the dashboard.
   - **Read:** at transport construction, `ParamMemo.load(json.loads(config.get(...)))`, wrapped so
     malformed JSON logs and yields an empty memo. A corrupt memo must never break model calls.
   - **Write:** a small `persist_quirks(memo)` writing the `PlatformSetting` row through
     `PlatformSettingRepo.upsert(...)` with `updated_by=None` (learned by the system, not an admin),
     then refreshing the DB provider so the value is live. Deliberately **not** through
     `ConfigAdminService.set` — that path writes an admin audit entry, and a transport-learned fact
     is not an administrator action.
   - **Fail-soft is mandatory.** A failed write is logged at `info` and swallowed: the in-process
     memo still holds the fix, so the build continues and the only cost is re-learning after a
     restart. Persistence is an optimisation; the call working is not.

4. **Bound the repair loop (D7 / Golden Rule 5).** `openai_max_param_repairs` (default 4) caps
   repairs **per call**, counted separately from `openai_max_retries` so a repair never consumes a
   transient-failure retry and vice versa. Each repair must be *novel* — the same
   `(adaptation, param)` twice means the fix did not take, so the loop stops and the error is
   raised. Termination is therefore guaranteed twice over: by the cap and by non-repetition.

5. **Config (`config.py` + `config_registry.py` + `.env.example`).**

   | Key | Default | Purpose |
   |---|---|---|
   | `llm_param_quirks` | `""` | learned per-(endpoint, model) request repairs (JSON) |
   | `openai_max_param_repairs` | 4 | per-call cap on request repairs |
   | `openai_param_learning` | `True` | master switch — off ⇒ seed table only, nothing learned or written |

### Commit 2 — wiring

6. **`build_openai_params` takes the repairs.** Signature becomes
   `build_openai_params(request, *, repairs: Iterable[Repair] = ()) -> dict`, applying them after
   building the canonical body. The `stream_usage` keyword is **removed**: "this endpoint refuses
   `stream_options`" is now just a `drop` repair like any other. `_NO_STREAM_USAGE` and
   `_rejects_stream_options` are deleted, and `tests/agents/test_openai_strict_endpoints.py` is
   rewritten against the general mechanism — the Mistral behaviour it guards **must** be preserved,
   so those assertions stay, only their route changes.

7. **`OpenAITransport.stream` gains the repair branch.** In the existing `except openai.OpenAIError`
   handler, *before* the transient check:

   ```
   if not produced and (repair := diagnose_param_error(exc)) and repairs_used < cap and repair is novel:
       memo.learn(endpoint, model, repair);  persist;  params = apply_repairs(params, [repair])
       continue                                  # does NOT consume a transient retry
   ```

   `not produced` is essential and already the rule the `stream_options` branch follows: once tokens
   have reached the UI the turn cannot be restarted without duplicating streamed output.

8. **Non-streaming fallback (`no_stream`).** The one repair that changes the *response* path, not
   just the request. When streaming is refused, the call is made with `stream=False` and the single
   response object is folded into the same `TextDelta` + `TurnComplete` events the loop expects —
   one `TextDelta` with the whole text, then the terminal event with tool calls and usage. A new
   `_from_completion(response) -> Iterator[StreamEvent]` beside `_accumulate` (`:499`), sharing
   `_parse_tool_args` and the finish-reason mapping so the two paths cannot drift. The loop, the
   agents and cost accounting see no difference; only live token streaming is lost, which is
   strictly better than the stage failing.

9. **Error message when repair is impossible.** `_provider_message` (`:600-620`) gains one branch:
   an unrepairable parameter 400 currently reads as a flat "the provider rejected the request",
   which is what made the reported failure opaque. It should name the parameter, the model, and the
   actionable next step (point `MODEL_CODEGEN` / `MODEL_ROUTING` elsewhere, or clear
   `llm_param_quirks` if a stale learned repair is suspected).

## Out of scope for this phase

- **An admin per-model param editor** — the user chose backend-only. `llm_param_quirks` is already
  visible and clearable in the dashboard as a JSON field, which covers the escape hatch.
- **Touching `SdkTransport` / the Anthropic path.** `MessageRequest` is Anthropic-shaped; that is
  the reference format and it is not adapted to itself.
- **Changing `MessageRequest`.** Renaming its `max_tokens` field would touch every agent and both
  transports to fix one provider's spelling. Translation belongs in the adapter — that is the whole
  point of the seam.
- **Reasoning-specific response handling** (`reasoning_content`, `reasoning_effort`, encrypted
  reasoning items). Real, but a *capability* feature, not a compatibility fix; it belongs with
  whatever phase adds reasoning-model support properly.
- **Non-OpenAI-shaped APIs** (raw Gemini, Bedrock). `LLM_PROVIDER` covers `anthropic` | `openai`;
  a third wire format is a new transport, not this.

## Acceptance criteria

- A model that rejects `max_tokens` (the reported `gpt-5.4-mini` failure) **completes normally**:
  the transport renames to `max_completion_tokens`, retries, and the stage succeeds — with the
  output cap still applied, not dropped.
- The repair is **learned once**: a second call with the same `(endpoint, model)` sends the correct
  body first time, and after a restart still does, because it was persisted.
- The same mechanism handles a **dropped** param (`temperature`), a **message-shape** change
  (`system` → `developer`) and a **streaming refusal** (falls back to non-streamed and still yields
  text, tool calls and usage) — assertable without a network.
- An **unattributable** 400 still fails fast with a clear message and **no** retry, exactly as today.
- Every path is bounded: `openai_max_param_repairs` per call, repairs must be novel, and the
  transient-retry ladder is untouched and separately counted.
- **Mistral/Azure `stream_options` behaviour is preserved** through the general mechanism, with its
  existing tests still asserting it.
- `LLM_PROVIDER=anthropic` is **byte-identical** — `SdkTransport` is not touched, and its tests pass
  unchanged.
- A corrupt or stale `llm_param_quirks` value degrades to an empty memo and re-learns; it can never
  break a model call.
- `ruff` / `black` / `mypy` clean; `pytest` green.

## Tests to write

**Control plane (`pytest`)** — no network, no API key; `pytest.importorskip("openai")` for the
error types, as the existing strict-endpoint suite does.

- `tests/agents/test_provider_param_diagnosis.py` *(new)* — table-driven over real provider error
  bodies: the **exact reported `gpt-5.4-mini` payload** → `rename(max_tokens →
  max_completion_tokens)`; `unsupported_value` on `temperature` → `drop`; Mistral's
  `422 extra_forbidden` on `stream_options` → `drop`; a streaming-refusal 400 → `no_stream`; a
  generic `400 invalid request` and a 500 → `None` (**the safety case — no blind retry**); a message
  naming a param we never sent → `None`.
- `tests/agents/test_provider_param_apply.py` *(new)* — `rename` preserves the *value* (the cap is
  not silently lost); `drop` removes only its own key; `developer_role` rewrites only the leading
  system message and leaves the transcript intact; `no_stream` clears `stream_options` too; repairs
  compose in any order to the same body.
- `tests/agents/test_provider_param_memo.py` *(new)* — the memo is keyed by `(endpoint, model)`, so
  a quirk learned for `gpt-5*` does **not** apply to `gpt-4o` on the same base URL; `snapshot`/`load`
  round-trip; malformed persisted JSON yields an empty memo without raising.
- `tests/agents/test_openai_param_repair_loop.py` *(new)* — a fake client that 400s once then
  succeeds: the call completes, the body was repaired, and **no transient retry was consumed**;
  repeating the identical rejection stops at the cap instead of looping; a failure *after* tokens
  have been produced is never repaired-and-retried (no duplicated output).
- `tests/agents/test_openai_non_streaming_fallback.py` *(new)* — a non-streamed completion with text
  + tool calls + usage folds into the same events as the streamed path, so the loop cannot tell them
  apart.
- **Re-run / adjust:** `tests/agents/test_openai_strict_endpoints.py` (rewritten onto the general
  mechanism, same guarantees), `tests/agents/test_openai_transport.py`,
  `tests/config/test_registry_complete.py`.

**Frontend** — none. No API or UI surface changes.

## Manual verification steps

```bash
cd backend && uv run pytest -q
uv run ruff check . && uv run black --check . && uv run mypy .
```

Then, with a real key:

1. Set `LLM_PROVIDER=openai` and point `MODEL_ROUTING` at the reasoning-family model that failed.
   Run any stage. Expect it to **succeed**, with one `info` log naming the repair it applied.
2. Check the admin panel → the `llm_param_quirks` value now contains the learned entry.
3. Restart the API and run again — expect **no** repair log the second time (the memo was persisted
   and applied up front).
4. Set `LLM_PROVIDER=anthropic` and run the same stage: unchanged behaviour, no repair machinery on
   the path at all.

## Rollback / cleanup notes

- **Code reverts cleanly.** The adapter is a new module; the transport change is contained to
  `build_openai_params` and one `except` branch.
- **`llm_param_quirks` rows persist** after a revert as an unread `PlatformSetting`. Harmless;
  delete the row to be tidy.
- **Reverting restores the `max_tokens` failure** for reasoning-family models — that is the bug this
  phase exists to fix, so revert deliberately, not incidentally.
- Removing the config keys is safe, but drop their `config_registry.py` entries in the same revert
  or `test_registry_complete.py` fails in the opposite direction.

## Design notes

- **Why the provider's error is the source of truth.** A static compatibility table is a guess about
  the future that is wrong the moment a vendor ships a model — which is precisely how this bug
  arrived. The error body, by contrast, is authoritative, self-describing, and already carries
  `param` and `code`. Making the error the primary input is what converts "supports the models we
  knew about" into "supports the endpoint you pointed it at". The seed table is kept only to spare
  the first call on families we already know, and is explicitly allowed to be wrong.
- **Rename, never drop, a cap.** Dropping `max_tokens` would make the call succeed while silently
  uncapping output length — a cost regression disguised as a fix, on the very path D13 measures.
  `rename` is a distinct adaptation for that reason.
- **Why `(endpoint, model)` and not `endpoint`.** `stream_options` is an endpoint-wide schema fact,
  so the existing memo's coarse key was fine. `max_tokens` is a *model* fact: same base URL,
  opposite answers for `gpt-4o` and `gpt-5*`. Widening the key is required for correctness, and it
  subsumes the old behaviour rather than replacing it.
- **Only pre-output failures are repaired.** Already the rule for `stream_options`, and it matters
  more here: a retry after tokens have reached the UI duplicates streamed output. `produced`
  gates it.
- **Unattributable 400s stay fatal.** The temptation is to retry any 400 with something removed.
  That turns a fast, clear failure into a slow, confusing one and can mask genuine bugs in our own
  request construction. `diagnose_param_error` returning `None` is a feature.
- **Persistence is an optimisation, not a dependency.** Everything works with the write failing —
  the process memo still repairs the call. This keeps a Mongo hiccup from breaking model calls,
  matching `config_db`'s stated fail-soft posture.
- **The `stream_options` special case is retired, not kept alongside.** Leaving both would mean two
  mechanisms for one problem drifting apart — exactly the duplication the phase-55 notes warned
  about with the repair loop. Its tests survive as the regression guard that the general mechanism
  really does cover the specific case.

## Implementation status (as-built, 2026-08-15)

**Done — every task 1–9 landed**, in one commit rather than the planned two (the adapter is inert
until wired, so splitting bought nothing and left a commit whose tests exercised dead code).

- **`app/agents/provider_compat.py` (new, pure).** `Adaptation` / `Repair` / `diagnose_param_error`
  / `apply_repairs` / `seed_repairs` / `ParamMemo` / `parse_quirks` / `persist_quirks`.
  `diagnose_param_error` reads `error.param` + `error.code` first and the message text second,
  strips provider path prefixes (`messages.0.role` → `role`), and matches an unnamed parameter only
  against **keys actually in the request**, longest-first.
- **Persistence** is the `llm_param_quirks` json config key, written through `PlatformSettingRepo`
  with `updated_by=None` and followed by a DB-provider reload so it is live without a restart.
  Wrapped so a failure is logged at `info` and swallowed.
- **`build_openai_params(request, *, repairs=())`** — the `stream_usage` keyword is gone;
  `stream_options` is now requested unconditionally and removed by an ordinary `drop` repair.
  `_NO_STREAM_USAGE` and `_rejects_stream_options` are deleted.
- **`OpenAITransport.run(request, create, *, endpoint, error_type)`** — the retry/repair loop with
  the network injected, which is what makes the loop testable without an SDK client;
  `stream()` is now a thin real-network wrapper around it.
- **`_from_completion`** folds a non-streamed completion into the same events as `_accumulate`,
  sharing `_parse_tool_args` and `_STOP_REASONS` so the two paths cannot drift.
- **Config:** `llm_param_quirks`, `openai_max_param_repairs` (4), `openai_param_learning` (true),
  registered and in `.env.example`.

**Two things found while testing, both fixed:**

1. **A rename could silently degrade to a drop.** `_REPLACEMENT` was searched only in
   `error.message`; SDKs vary over whether the full sentence lands there or in `str(exc)`, and a
   summary-only `message` produced a `drop` — which would have uncapped output length instead of
   renaming the cap. It now searches both, and the "the cap survived the rename" assertion is what
   catches it.
2. **The seed table needed a negative test.** A seed rule wrongly matching an ordinary model would
   break a provider that was working, so `test_ordinary_models_are_not_seeded` pins `gpt-4o`,
   `claude-sonnet-5`, `mistral-large-latest` and `llama-3.1-70b` as unseeded.

**Tests (all pass, none needs a network or a key):** `test_provider_param_diagnosis.py` (15 — the
**verbatim reported payload** first, then each other class, then the whole unattributable/safety
half), `test_provider_param_apply.py` (12), `test_provider_param_memo.py` (10),
`test_openai_param_repair_loop.py` (8 — the reported bug fixed end to end, repairs not consuming
retries, learn-once, and all three bounds), `test_openai_non_streaming_fallback.py` (6).
`test_openai_strict_endpoints.py` rewritten onto the general mechanism with its Mistral guarantees
intact.

**Verification performed:** `tests/agents` + `tests/config` → **296 passed, 6 failed**;
`tests/requirements` + `tests/eval` → **143 passed**. All 6 failures are the pre-existing
Docker-socket ones (`test_repair_fixes_seeded_bug`, `test_repair_minimality`,
`test_repair_regression_report`), unrelated and identically red before this phase. `ruff` / `black`
clean repo-wide; `mypy` clean on both changed modules.

**Remaining / follow-ups (not blockers):**

- **The real-provider run has not been done here** — no OpenAI key on this box. The "Manual
  verification steps" above (especially step 3, that a restart applies the learned repair up front)
  should be run against the actual `gpt-5.4-mini` endpoint before relying on it.
- **Seed coverage is deliberately minimal** (OpenAI reasoning families only). Other providers are
  handled reactively by design; add a seed rule only if a wasted first call proves annoying.
- **`developer_role` and `no_stream` are unproven against a live endpoint.** Their logic and event
  shape are unit-tested, but unlike the `max_tokens` case they were not reproduced from a real
  failure.
