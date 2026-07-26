# ForgeFlow — Pre-release Hardening Checklist

Run before a demo, a release, or any deployment reachable by someone other than you.
Companion to [`threat-model.md`](./threat-model.md).

Most items are **automated** — `✅ auto` means a test fails if the property regresses, so the check
is a matter of running the suite rather than clicking through the app. Items marked **manual**
need a human because they concern the deployed environment, not the code.

```bash
# The automated portion, in one command:
cd backend && uv run pytest tests/security -q

# Including the live-daemon container inspection (needs Docker + the sandbox image):
cd backend && uv run pytest tests/security -q -m docker
```

---

## 1. Sandbox isolation

| ✔ | Check | How |
|---|-------|-----|
| ✅ auto | Sealed **per-project `internal` network** by default — no host network, no internet, no other sandbox on it (not `none`: docker cannot attach preview/egress to a `none`-mode container) | `test_sandbox_isolation.py` |
| ✅ auto | `cap_drop=[ALL]` and `no-new-privileges` | `test_sandbox_isolation.py` |
| ✅ auto | Read-only root; writes only to `/workspace`, `/home/app` (package-manager caches) + `tmpfs /tmp` | `test_sandbox_isolation.py` |
| ✅ auto | `pids_limit`, `mem_limit`, `nano_cpus` set **from config** (not hardcoded) | `test_sandbox_isolation.py` |
| ✅ auto | No `privileged`, `cap_add`, `network_mode: host`, or docker-socket mount anywhere in `app/` | `test_sandbox_isolation.py` |
| ✅ auto | Only named volumes are mounted — no host bind mounts | `test_sandbox_isolation.py` |
| ✅ auto | Preview network is `internal=True` | `test_sandbox_isolation.py` |
| ✅ auto | Egress network is a bridge (never host) and labelled | `test_sandbox_isolation.py` |
| ✅ auto (`-m docker`) | A real container's `docker inspect` shows all of the above | `test_sandbox_isolation.py` |
| ✅ auto | Path traversal out of `/workspace` is refused | phase-12 `test_path_safety.py` |
| **manual** | The sandbox image runs as a **non-root** user (`docker run … whoami` ≠ root) | `sandbox/Dockerfile` |
| **manual** | Idle reaper is running: leave a sandbox idle past `SANDBOX_IDLE_TIMEOUT_S`, confirm it stops | observe |
| **manual** | Egress network is detached after a live validation run (`docker network inspect`) | observe |

## 2. Authentication & authorization

| ✔ | Check | How |
|---|-------|-----|
| ✅ auto | Every route has an auth dependency; the public surface is exactly `/health`, `/auth/register`, `/auth/login` | `test_authz_matrix.py` |
| ✅ auto | No route is reachable without a token (401) | `test_authz_matrix.py` |
| ✅ auto | No project-scoped route serves another user's project | `test_authz_matrix.py` |
| ✅ auto | Cross-user reads return **404, not 403** (existence not leaked) | `test_authz_matrix.py` |
| ✅ auto | `/admin/*` requires the admin role | `test_authz_matrix.py` |
| ✅ auto | Forged/malformed tokens are rejected | `test_authz_matrix.py` |
| ✅ auto | Project listing, artifacts and credentials are per-user | `test_authz_matrix.py` |
| ✅ auto | WebSockets authenticate + authorize before `accept()` | phase-05 `test_ws_authz.py` |
| **manual** | `SECRET_KEY` is not the shipped dev default in any deployed environment | check env |
| **manual** | Token lifetime (`ACCESS_TOKEN_EXPIRE_MINUTES`) is appropriate for the deployment | check env |

## 3. Secrets

| ✔ | Check | How |
|---|-------|-----|
| ✅ auto | Credentials are ciphertext at rest | `test_secret_nonleak.py` |
| ✅ auto | No secret appears in any response body | `test_secret_nonleak.py` |
| ✅ auto | No secret appears in any log line | `test_secret_nonleak.py` |
| ✅ auto | No secret appears in an error envelope | `test_secret_nonleak.py` |
| ✅ auto | Credential responses expose only `{kind, scope, created_at, last4}` | `test_secret_nonleak.py` |
| ✅ auto | No credential-shaped literal in `app/` | `test_secret_nonleak.py` |
| ✅ auto | `.env.example` ships no populated secret values | `test_secret_nonleak.py` |
| ✅ auto | Seamless mode never spends a user's BYO token; BYO never falls back to the platform's | phase-35 `test_modes.py` |
| ✅ CI | Repo-wide secret scan (gitleaks) on every push/PR | `.github/workflows/ci.yml` |
| **manual** | `FERNET_KEY` is set, unique per environment, and **backed up** (rotation destroys stored credentials) | check env |
| **manual** | `.env` is not committed and not baked into any image | `git log`, image inspect |
| **manual** | Provider tokens are scoped to least privilege in the provider's own console | provider console |

## 4. Abuse & resource limits

| ✔ | Check | How |
|---|-------|-----|
| ✅ auto | `/auth/*` is rate limited per IP | `test_rate_limits.py` |
| ✅ auto | All 8 expensive endpoints are rate limited per user | `test_rate_limits.py` |
| ✅ auto | One user's burst does not limit another | `test_rate_limits.py` |
| ✅ auto | Exhausting the expensive budget does not block logging in | `test_rate_limits.py` |
| ✅ auto | Over-sized bodies are refused with 413 | `test_rate_limits.py` |
| ✅ auto | The body cap still admits a full design-image batch | `test_rate_limits.py` |
| ✅ auto | Data-browser queries: operator allowlist, depth/size/page caps, `maxTimeMS` | phase-41 tests |
| ✅ auto | The repair loop is bounded and escalates to a human | phase-31 tests |
| **manual** | Budget caps (`BUDGET_CAP_INR_*`) are set for any shared deployment | check env |
| **manual** | Rate limits are appropriate for the audience (demo vs public) | check env |

## 5. Input validation

| ✔ | Check | How |
|---|-------|-----|
| ✅ auto | Workspace paths reject absolute, `~`, `..`, and NUL | phase-12 `test_path_safety.py` |
| ✅ auto | Mongo filters refuse JS-evaluating operators and unknown operators | phase-41 tests |
| ✅ auto | Mongo URI construction resists injection | phase-36 `test_uri_injection.py` |
| ✅ auto | Collection names exclude `system.*` and `$` | phase-41 tests |
| ✅ auto | Request bodies are typed Pydantic schemas at every boundary | 422 coverage in `test_authz_matrix.py` |
| **manual** | Design image uploads honour count and per-file byte caps | exercise the UI |

## 6. Deployment posture (manual — environment, not code)

| ✔ | Check |
|---|-------|
| ☐ | `CORS_ORIGINS` is an explicit allowlist — **not** `*` — in any non-local deployment |
| ☐ | TLS terminates in front of the control plane; no plaintext HTTP |
| ☐ | Mongo is not exposed to the public internet; authentication is enabled |
| ☐ | The Docker daemon socket is not exposed over TCP |
| ☐ | `FORGEFLOW_ENV` is set correctly; `/docs` exposure is intentional for the audience |
| ☐ | Logs ship somewhere durable and access-controlled (they contain project metadata) |
| ☐ | Container images are rebuilt from a current base (no stale CVEs) |

## 7. Supply chain

| ✔ | Check | How |
|---|-------|-----|
| ✅ CI | Python dependency audit (`pip-audit`) | `.github/workflows/ci.yml` |
| ✅ CI | Node dependency audit (`pnpm audit`) | `.github/workflows/ci.yml` |
| ✅ CI | Secret scan across history | `.github/workflows/ci.yml` |
| **manual** | Review audit findings before release; pin or patch anything high/critical |

---

## Sign-off

| Field | Value |
|-------|-------|
| Release / demo | |
| Date | |
| Automated suite (`pytest tests/security`) | ☐ pass |
| Docker-marked isolation test | ☐ pass ☐ n/a |
| Manual items reviewed | ☐ |
| Residual risks re-read and still accepted (threat model §6) | ☐ |
| Signed off by | |
