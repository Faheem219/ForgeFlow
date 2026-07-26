# ForgeFlow — Threat Model

**Status:** current as of phase-47 (Epic 11, security & sandbox hardening pass).
**Scope:** the ForgeFlow control plane, its sandboxes, and the provider integrations it drives.
**Companion:** [`hardening-checklist.md`](./hardening-checklist.md) — the pre-release checklist whose
automatable items are enforced by `backend/tests/security/`.

---

## 1. What makes ForgeFlow unusual

Most web applications never execute code they did not ship. ForgeFlow does so by design: an LLM
writes an application, and ForgeFlow builds, runs, tests and deploys it. **Generated code is
untrusted input, not a trusted artifact**, and every control below follows from that.

The second unusual property is that ForgeFlow holds credentials for *other* systems — a user's
Vercel token, a Render key, a Mongo Atlas URI. A breach is therefore not only a breach of ForgeFlow.

---

## 2. Assets

| # | Asset | Why it matters | Where it lives |
|---|-------|----------------|----------------|
| A1 | **Provider credentials** (Vercel, Render, Mongo URI, Stitch, Figma) | Compromise extends into the user's own cloud accounts | `credentials` collection, Fernet-encrypted (`app/deploy/secrets.py`) |
| A2 | **Platform credentials** (Anthropic key, platform provider tokens) | Direct financial loss; enables abuse at our expense | Environment / layered config, or a platform-scoped vault row |
| A3 | **`FERNET_KEY`** | Decrypts every stored credential — the crown jewel | Environment only; never in the database |
| A4 | **User accounts + JWT signing key** | Account takeover across every project | `users` collection (bcrypt), `SECRET_KEY` in env |
| A5 | **Project data** (source, artifacts, conversations, per-project app DB) | User IP and, in generated apps, end-user data | Control-plane DB + per-project databases |
| A6 | **Host running the control plane** | Full compromise of everything above | Docker host |
| A7 | **Model budget** | Runaway spend is a real denial-of-wallet | `Run` accounting (phase-20/46) |

---

## 3. Actors

- **Legitimate user** — authenticated; owns some projects. *Assumed to be curious and occasionally
  hostile toward other tenants.*
- **Anonymous internet** — reaches only the public surface (`/health`, `/auth/register`,
  `/auth/login`) plus whatever CORS permits.
- **Generated code** — the LLM's output, running with the user's intent but **never** their
  authority. Treated as hostile: it may be buggy, prompt-injected, or deliberately malicious.
- **Compromised provider** — Anthropic/Stitch/Figma/Vercel/Render returning hostile responses.
- **Operator** — deploys and configures the platform; trusted, but assumed to make mistakes
  (which is why misconfiguration is a modelled risk, not an excluded one).

---

## 4. Trust boundaries

```
      anonymous internet
              │  TLS, CORS allowlist, per-IP rate limit, body-size cap
              ▼
   ┌─────────────────────────┐
   │   CONTROL PLANE         │  trusted: our code, our config
   │   FastAPI + Mongo       │  holds A1–A5; enforces authn + ownership
   └──┬───────────────┬──────┘
      │ B1            │ B2                        B3
      ▼               ▼                            ▼
 ┌──────────┐   ┌──────────────┐         ┌───────────────────┐
 │ SANDBOX  │   │ per-project  │         │ external providers │
 │ (docker) │   │  databases   │         │ Anthropic/Vercel/… │
 │ UNTRUSTED│   │  isolated    │         │  semi-trusted      │
 └──────────┘   └──────────────┘         └───────────────────┘
```

- **B1 — control plane ⇄ sandbox.** The most important boundary in the system. Crossed only by the
  workspace-FS API, the exec API, and the preview proxy. **Generated code never runs in the control
  plane** (Golden Rule 3).
- **B2 — control plane ⇄ project data.** Every project has its own database; the data browser is the
  only path in, and it is ownership-checked and query-guarded.
- **B3 — control plane ⇄ providers.** Outbound only. Credentials are decrypted at call time and
  never logged; provider responses are parsed defensively and never echoed into errors.

---

## 5. Attack surfaces, threats and mitigations

### 5.1 Sandbox escape — *the primary risk (risk §12)*

| Threat | Mitigation | Verified by |
|---|---|---|
| Generated code reaches the host network / other services | Sealed per-project `internal` network by default (no route out); preview + registry-egress attached deliberately and temporarily | `test_sandbox_isolation.py`, `test_install_egress.py` |
| Privilege escalation inside the container | `cap_drop=[ALL]`, `security_opt=[no-new-privileges]`, non-root user | `test_sandbox_isolation.py` |
| Tampering with the runtime image | `read_only` root; writes confined to `/workspace`, `/home/app` + `tmpfs /tmp` | `test_sandbox_isolation.py` |
| Resource exhaustion (fork bomb, memory, CPU) | `pids_limit`, `mem_limit`, `nano_cpus`; exec timeouts; idle reaping | `test_sandbox_isolation.py`, phase-11/13 tests |
| Docker-socket escape | The socket is never mounted; asserted by a source-wide scan | `test_sandbox_isolation.py` |
| Path traversal out of `/workspace` | `safe_rel_path()` chokepoint (lexical) + symlink resolution at call time | phase-12 `test_path_safety.py` |

**Deliberate relaxations** — both are audited and narrow:

1. **Preview network** (phase-15): `internal=True`. Containers reach each other; the network has
   *no route to the host or internet*.
2. **Egress network** (phase-39): `internal=False` — live validation must reach the deployed public
   URL. This is the widest relaxation in the system, so it is narrow *in time*: attached for one
   live run, detached in a `finally`. It is still a routed bridge, never the host namespace.

### 5.2 Cross-tenant access

| Threat | Mitigation | Verified by |
|---|---|---|
| Reading another user's project, artifacts, data, deployments | Ownership resolved on **every** project-scoped route (`get_owned`) | `test_authz_matrix.py` — swept across all discovered routes |
| Enumerating which projects exist | Non-owned resources return **404, never 403** | `test_authz_matrix.py` |
| Unauthenticated access | Auth dependency on every route; public surface pinned to 3 endpoints | `test_authz_matrix.py` |
| Privilege escalation to admin | `require_admin` on `/admin/*` | `test_authz_matrix.py` |
| WebSocket bypass of HTTP authz | WS handlers authenticate the token and check ownership before `accept()` (close 4401/4403) | phase-05 `test_ws_authz.py` |
| Cross-project DB access | Per-project databases; the data browser resolves the DB from the owned project only | phase-41 tests |

### 5.3 Secret disclosure

| Threat | Mitigation | Verified by |
|---|---|---|
| Secrets at rest in Mongo | Fernet encryption; only ciphertext is stored | `test_secret_nonleak.py` |
| Secrets returned to the client | No read-back route exists; responses carry `{kind, scope, created_at, last4}` only | `test_secret_nonleak.py` |
| Secrets in logs | The vault never logs values; `JsonFormatter` redacts credential-shaped extras | `test_secret_nonleak.py` |
| Secrets in error messages | Provider response bodies are never echoed into errors (they can contain env values) | phase-35 adapters |
| Secrets committed to the repo | `.env` is git-ignored; `.env.example` must ship blank; CI secret scan | `test_secret_nonleak.py`, CI |
| A user's BYO token spent by the platform | `seamless` resolves the platform credential *only*; `byo` never falls back | phase-35 `test_modes.py` |
| Secrets leaking into the browser bundle | The infra analyzer refuses to classify a `VITE_`-prefixed var as a secret and warns if one looks secret-shaped | phase-33 tests |

### 5.4 Abuse, denial of service, denial of wallet

| Threat | Mitigation | Verified by |
|---|---|---|
| Credential stuffing / registration spam | Per-IP fixed-window limit on `/auth/*` | `test_rate_limits.py` |
| Burning model budget or sandbox capacity | **Per-user** limit on the 8 expensive endpoints; per-project + global budget caps (phase-20/46) | `test_rate_limits.py` |
| Memory exhaustion via a huge body | Global `max_request_bytes` cap → 413 before buffering; tighter per-route caps | `test_rate_limits.py` |
| Unbounded repair spend | The repair loop is bounded: max iterations, no-progress detection, human escalation (Golden Rule 5) | phase-31 tests |
| Expensive DB queries from the data browser | Operator allowlist, depth/size caps, page cap, server-side `maxTimeMS` | phase-41 tests |
| Noisy neighbour starving others | Rate buckets are keyed per user; auth and expensive budgets are separate buckets | `test_rate_limits.py` |

### 5.5 Injection

| Threat | Mitigation | Verified by |
|---|---|---|
| NoSQL injection / server-side JS via data-browser filters | **Allowlist** of data-only operators; `$where`/`$function`/`$accumulator` refused by name; unknown operators fail closed | phase-41 `guards.py` tests |
| Mongo URI injection during provisioning | URI construction is validated/escaped | phase-36 `test_uri_injection.py` |
| Command injection into the sandbox | Exec is argument-based with timeouts, inside the sandbox — never the control plane | phase-13 tests |
| Prompt injection steering the agent | Agents are tool-scoped to the sandbox; tools cannot touch the control plane or the vault; the repair loop is bounded and human-escalating | phase-21/31 |

---

## 6. Residual risks (accepted, with reasons)

| # | Risk | Why accepted | Compensating control |
|---|---|---|---|
| R1 | **Rate limits are in-process** | Single-instance dev/demo target; a distributed limiter is unjustified complexity now | `RateLimiter` is swappable behind the same dependency (Redis-backed drop-in) |
| R2 | **Egress network during live validation** | Validating a deployed URL is the point of the stage; there is no way to do it without egress | Attached for one run, detached in `finally`; bridge, never host |
| R3 | **Rotating `FERNET_KEY` destroys stored credentials** | Key-wrapping/rotation machinery is disproportionate at this scale | Documented loudly in `.env.example`; decryption failure degrades to "missing", never a crash |
| R4 | **`/eval/*` is readable by any authenticated user** | Benchmark results are platform telemetry, not user data | No user data is exposed; tighten to `require_admin` if the harness ever runs user projects |
| R5 | **Generated code can reach the internet during live validation** | Same as R2 | Time-bounded; generated code is already assumed hostile and confined |
| R6 | **Sandbox shares the host kernel** | gVisor/Firecracker is out of scope for this project | Full capability drop, no-new-privileges, read-only root, resource caps |
| R7 | **Providers see project content** | Inherent to using a hosted LLM and hosted deploy targets | Only what a stage needs is sent; secrets are never in prompts |

---

## 7. What is explicitly *not* defended against

- A malicious **operator** with host or database access.
- Compromise of **Anthropic, Vercel, Render or MongoDB Atlas** themselves.
- Kernel-level container escapes (0-days in Docker/runc) — see R6.
- Physical access to the host.

---

## 8. Keeping this current

Update this document when any of these change: a new route family, a new external provider, a new
network relaxation, a change to credential storage, or a new class of untrusted input. The
`backend/tests/security/` suite discovers routes from the live app, so **new endpoints are swept
automatically** — but a new *trust boundary* still needs a human to record it here.
