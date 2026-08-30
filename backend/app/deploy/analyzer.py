"""Infra analyzer (phase-33) — classify what a project needs to run in production.

Beginner-safe infra is the secondary differentiator (D1): the user should never have to know that a
Vite SPA is static hosting while an Express server needs a persistent process. The analyzer reads
the workspace and says so, producing the ``InfraPlan`` that drives dual-mode deploy (D11: FE→Vercel,
BE→Render, DB→Atlas).

**Static-analysis-first** (design note): every rule here is deterministic and model-free, so results
are cheap and reproducible for the eval harness. Where the evidence genuinely conflicts the analyzer
**flags it** (``warnings`` + a lowered ``confidence``) for the user to confirm rather than guessing
silently — the human-in-the-loop invariant (D12). A Haiku fallback is deliberately *not* wired: it
would trade reproducibility and cost for cases the flags already surface honestly.

One rule is load-bearing for security: anything ``VITE_``-prefixed is compiled into the browser
bundle, so it can never be a secret. The analyzer refuses to classify one as such and warns if a
``VITE_`` name looks secret-shaped.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from beanie import PydanticObjectId

from app.core.config import get_config
from app.core.errors import NotFoundError, UserError
from app.db.models import Project
from app.db.models.enums import ArtifactType, Stage
from app.orchestrator.artifacts import ArtifactService
from app.sandbox.schemas import FileContent, FileNode

INFRA_PLAN_KIND = "infra_plan"

# Targets follow D11 (amended in phase-58) — the analyzer classifies the *shape*, not the vendor.
# The backend's actual vendor is resolved at deploy time from DEPLOY_BE_PROVIDER; this is the label
# for the default route, and the recorded Deployment always carries the provider that really ran.
TARGET_FE = "vercel"
TARGET_BE = "vercel"
TARGET_DB = "atlas"

# Where the fixed-stack skeleton (phase-22) puts things; the analyzer also tolerates a
# single-package project at the workspace root (an FE-only app).
_FE_DIRS = ("frontend", "")
_BE_DIRS = ("backend", "server", "api")

_SOURCE_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".mts", ".cts")

# `import.meta.env.VITE_FOO` / `import.meta.env['VITE_FOO']`
_VITE_ENV_RE = re.compile(r"import\.meta\.env\.([A-Z_][A-Z0-9_]*)")
# `process.env.FOO` / `process.env['FOO']`
_PROCESS_ENV_RE = re.compile(
    r"process\.env(?:\.([A-Z_][A-Z0-9_]*)|\[['\"]([A-Z_][A-Z0-9_]*)['\"]\])"
)
# A `KEY=value` line in a .env file (per line, so comments/blank lines are skipped).
_ENV_LINE_RE = re.compile(r"^\s*([A-Z_][A-Z0-9_]*)\s*=", re.MULTILINE)
# `outDir: 'build'` in a vite config.
_OUT_DIR_RE = re.compile(r"outDir\s*:\s*['\"]([^'\"]+)['\"]")
# `process.env.PORT ?? 3001` / `|| 3001`
_PORT_DEFAULT_RE = re.compile(r"PORT[^\n]{0,40}?(?:\?\?|\|\|)\s*(\d{2,5})")

# Names that must live in the secret vault (phase-34). Deliberately excludes `_URL`: a base URL is
# public config, while a `_URI` (mongodb://user:pass@…) carries credentials.
_SECRET_HINTS = (
    "SECRET",
    "TOKEN",
    "PASSWORD",
    "PASSWD",
    "_KEY",
    "APIKEY",
    "_URI",
    "DSN",
    "CREDENTIAL",
)

# Env names that are supplied by the platform at deploy time, not by the user.
_INJECTED = frozenset({"NODE_ENV", "PORT", "MONGODB_URI", "VITE_API_BASE_URL"})


def is_secret(name: str) -> bool:
    """True when an env var must be stored in the vault rather than as plain config."""
    if name.startswith("VITE_"):
        return False  # bundled into the browser — never a secret, by construction
    upper = name.upper()
    return any(hint in upper for hint in _SECRET_HINTS)


# --------------------------------------------------------------------- plan shape


@dataclass
class FrontendPlan:
    type: str = "static"
    dir: str = "frontend"
    install_cmd: str = "pnpm install"
    build_cmd: str = "pnpm build"
    output_dir: str = "dist"
    env: list[str] = field(default_factory=list)
    target: str = TARGET_FE

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "dir": self.dir,
            "install_cmd": self.install_cmd,
            "build_cmd": self.build_cmd,
            "output_dir": self.output_dir,
            "env": self.env,
            "target": self.target,
        }


@dataclass
class BackendPlan:
    type: str = "persistent"
    dir: str = "backend"
    install_cmd: str = "pnpm install"
    build_cmd: str | None = "pnpm build"
    start_cmd: str = "pnpm start"
    port: int = 3001
    env: list[str] = field(default_factory=list)
    target: str = TARGET_BE

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "dir": self.dir,
            "install_cmd": self.install_cmd,
            "build_cmd": self.build_cmd,
            "start_cmd": self.start_cmd,
            "port": self.port,
            "env": self.env,
            "target": self.target,
        }


@dataclass
class DatabasePlan:
    type: str = "mongo"
    provider: str = TARGET_DB  # atlas (platform-provisioned) | byo (user-supplied URI)
    env_var: str = "MONGODB_URI"

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "provider": self.provider, "env_var": self.env_var}


@dataclass
class InfraPlan:
    fe: FrontendPlan | None = None
    be: BackendPlan | None = None
    db: DatabasePlan | None = None
    required_secrets: list[str] = field(default_factory=list)
    # Ambiguities surfaced for the user to confirm rather than guessed at (D12).
    warnings: list[str] = field(default_factory=list)
    confidence: str = "high"  # high | medium | low
    notes: list[str] = field(default_factory=list)

    @property
    def needs_confirmation(self) -> bool:
        return bool(self.warnings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fe": self.fe.to_dict() if self.fe else None,
            "be": self.be.to_dict() if self.be else None,
            "db": self.db.to_dict() if self.db else None,
            "required_secrets": self.required_secrets,
            "warnings": self.warnings,
            "confidence": self.confidence,
            "notes": self.notes,
            "needs_confirmation": self.needs_confirmation,
        }


# --------------------------------------------------------------------- workspace seam


class AnalyzerWorkspace(Protocol):
    """The read-only slice of :class:`~app.sandbox.workspace.WorkspaceService` the analyzer uses."""

    async def read(self, project: Project, path: str) -> FileContent: ...

    async def tree(
        self, project: Project, path: str = ".", depth: int | None = None
    ) -> list[FileNode]: ...


# --------------------------------------------------------------------- analyzer


class InfraAnalyzer:
    def __init__(
        self,
        workspace: AnalyzerWorkspace | None = None,
        artifacts: ArtifactService | None = None,
    ) -> None:
        if workspace is None:
            from app.sandbox.workspace import WorkspaceService

            workspace = WorkspaceService()
        self._workspace = workspace
        self._artifacts = artifacts or ArtifactService()

    async def analyze(self, project: Project, *, persist: bool = True) -> InfraPlan:
        """Classify the workspace into a deployable plan."""
        plan = InfraPlan()

        fe_dir, fe_pkg = await self._find_package(project, _FE_DIRS, self._is_frontend)
        be_dir, be_pkg = await self._find_package(project, _BE_DIRS, self._is_backend)

        fe_env = await self._collect_env(project, fe_dir, frontend=True)
        be_env = await self._collect_env(project, be_dir, frontend=False) if be_pkg else set()

        if fe_pkg is not None:
            plan.fe = await self._frontend_plan(project, fe_dir, fe_pkg, fe_env)
        else:
            plan.warnings.append(
                "No frontend detected (no package.json with Vite) — confirm the frontend location."
            )

        if be_pkg is not None:
            plan.be = await self._backend_plan(project, be_dir, be_pkg, be_env)

        plan.db = self._database_plan(be_pkg, fe_env | be_env, plan)

        self._flag_ambiguities(plan, be_dir, be_pkg, fe_env | be_env)
        plan.required_secrets = sorted(
            {name for name in (fe_env | be_env) if is_secret(name)}
            | ({plan.db.env_var} if plan.db else set())
        )
        plan.confidence = _confidence(plan)

        if persist and project.id is not None:
            await self._persist(project.id, plan)
        return plan

    # -- detection -------------------------------------------------------------------------

    async def _find_package(
        self,
        project: Project,
        candidates: tuple[str, ...],
        matches: Any,
    ) -> tuple[str, dict[str, Any] | None]:
        """The first candidate dir whose package.json satisfies ``matches``."""
        for directory in candidates:
            path = f"{directory}/package.json" if directory else "package.json"
            pkg = await self._read_json(project, path)
            if pkg is not None and matches(pkg):
                return directory, pkg
        return candidates[0], None

    @staticmethod
    def _is_frontend(pkg: dict[str, Any]) -> bool:
        deps = _all_deps(pkg)
        if "vite" in deps:
            return True
        build = str(_scripts(pkg).get("build", ""))
        return "vite" in build

    @staticmethod
    def _is_backend(pkg: dict[str, Any]) -> bool:
        deps = _all_deps(pkg)
        return "express" in deps or "fastify" in deps or "koa" in deps

    async def _frontend_plan(
        self, project: Project, directory: str, pkg: dict[str, Any], env: set[str]
    ) -> FrontendPlan:
        scripts = _scripts(pkg)
        output_dir = await self._vite_out_dir(project, directory)
        return FrontendPlan(
            dir=directory,
            build_cmd=f"pnpm {'build' if 'build' in scripts else 'run build'}",
            output_dir=output_dir,
            env=sorted(env),
        )

    async def _backend_plan(
        self, project: Project, directory: str, pkg: dict[str, Any], env: set[str]
    ) -> BackendPlan:
        scripts = _scripts(pkg)
        # Prefer an explicit production start; fall back to dev so a plan is still actionable.
        start = "start" if "start" in scripts else ("dev" if "dev" in scripts else None)
        return BackendPlan(
            dir=directory,
            build_cmd="pnpm build" if "build" in scripts else None,
            start_cmd=f"pnpm {start}" if start else "node dist/index.js",
            port=await self._backend_port(project, directory),
            env=sorted(env),
        )

    def _database_plan(
        self, be_pkg: dict[str, Any] | None, env: set[str], plan: InfraPlan
    ) -> DatabasePlan | None:
        uses_mongoose = be_pkg is not None and "mongoose" in _all_deps(be_pkg)
        references_uri = "MONGODB_URI" in env
        if not uses_mongoose and not references_uri:
            return None
        if uses_mongoose and not references_uri:
            plan.warnings.append(
                "Mongoose is a dependency but MONGODB_URI is never read — confirm the database "
                "connection string."
            )
        if references_uri and not uses_mongoose:
            plan.notes.append("MONGODB_URI is referenced without Mongoose — assuming MongoDB.")
        return DatabasePlan()

    # -- env scanning ----------------------------------------------------------------------

    async def _collect_env(self, project: Project, directory: str, *, frontend: bool) -> set[str]:
        """Env names from the package's ``.env.example`` plus the code that actually reads them."""
        names: set[str] = set()
        for path in _env_example_paths(directory):
            content = await self._read_text(project, path)
            if content:
                names |= {m.group(1) for m in _ENV_LINE_RE.finditer(content)}

        src = f"{directory}/src" if directory else "src"
        for file in await self._source_files(project, src):
            content = await self._read_text(project, file)
            if not content:
                continue
            names |= {m.group(1) for m in _VITE_ENV_RE.finditer(content)}
            names |= {m.group(1) or m.group(2) for m in _PROCESS_ENV_RE.finditer(content)}

        # A frontend can only ever receive VITE_-prefixed vars; a backend never uses them.
        return (
            {n for n in names if n.startswith("VITE_")}
            if frontend
            else {n for n in names if not n.startswith("VITE_")}
        )

    async def _source_files(self, project: Project, root: str) -> list[str]:
        config = get_config()
        try:
            nodes = await self._workspace.tree(
                project, root, depth=int(config.get("infra_scan_max_depth"))
            )
        except Exception:  # a missing/unreadable source dir is simply no sources to scan
            return []
        files = [n.path for n in nodes if n.type == "file" and n.path.endswith(_SOURCE_SUFFIXES)]
        return files[: int(config.get("infra_scan_max_files"))]

    # -- small readers ---------------------------------------------------------------------

    async def _vite_out_dir(self, project: Project, directory: str) -> str:
        for name in ("vite.config.ts", "vite.config.js", "vite.config.mts"):
            path = f"{directory}/{name}" if directory else name
            content = await self._read_text(project, path)
            if content:
                match = _OUT_DIR_RE.search(content)
                if match:
                    return match.group(1)
        return "dist"  # Vite's default

    async def _backend_port(self, project: Project, directory: str) -> int:
        for name in ("src/config.ts", "src/index.ts", "src/server.ts"):
            path = f"{directory}/{name}" if directory else name
            content = await self._read_text(project, path)
            if content:
                match = _PORT_DEFAULT_RE.search(content)
                if match:
                    return int(match.group(1))
        return int(get_config().get("preview_be_port"))

    async def _read_text(self, project: Project, path: str) -> str | None:
        """The file's text, or ``None`` when the workspace genuinely has nothing usable there.

        Only *file-level* verdicts become ``None``: absent (``NotFoundError``), or present but
        unusable as config — too large, not UTF-8 (``UserError``). Anything else is the sandbox
        failing, not an answer about the file, and it propagates.

        This distinction is load-bearing. Swallowing every exception made a Docker hiccup on the
        first read indistinguishable from "there is no frontend here", and the deploy went on to
        ship a backend-only app and report it live (the missing node in the topology was the only
        hint). A cold sandbox now fails the analysis loudly, which is retryable; a wrong plan is
        not.
        """
        try:
            return (await self._workspace.read(project, path)).content
        except (NotFoundError, UserError):
            return None

    async def _read_json(self, project: Project, path: str) -> dict[str, Any] | None:
        content = await self._read_text(project, path)
        if not content:
            return None
        try:
            parsed = json.loads(content)
        except (ValueError, TypeError):
            return None
        return parsed if isinstance(parsed, dict) else None

    # -- flags + persistence ---------------------------------------------------------------

    def _flag_ambiguities(
        self, plan: InfraPlan, be_dir: str, be_pkg: dict[str, Any] | None, env: set[str]
    ) -> None:
        if plan.be is not None and plan.be.build_cmd is None:
            plan.warnings.append(
                f"The backend in '{be_dir}' has no build script — confirm how it is compiled."
            )
        if be_pkg is not None and not _scripts(be_pkg).get("start"):
            plan.notes.append(
                "No `start` script on the backend; falling back to its dev command for the plan."
            )
        # A VITE_ var is compiled into the browser bundle — a secret-shaped one is a real leak.
        for name in sorted(env):
            if name.startswith("VITE_") and any(h in name.upper() for h in _SECRET_HINTS):
                plan.warnings.append(
                    f"{name} is exposed to the browser (VITE_ prefix) but looks like a secret — "
                    "move it to the backend."
                )
        unknown = sorted(n for n in env if n not in _INJECTED and not n.startswith("VITE_"))
        if unknown:
            plan.notes.append("Environment values you may need to supply: " + ", ".join(unknown))

    async def _persist(self, project_id: PydanticObjectId, plan: InfraPlan) -> None:
        await self._artifacts.create_version(
            project_id,
            Stage.deploy,
            ArtifactType.infra_plan,
            text=json.dumps(plan.to_dict(), indent=2),
            meta={
                "kind": INFRA_PLAN_KIND,
                "confidence": plan.confidence,
                "needs_confirmation": plan.needs_confirmation,
                "targets": {
                    "fe": plan.fe.target if plan.fe else None,
                    "be": plan.be.target if plan.be else None,
                    "db": plan.db.provider if plan.db else None,
                },
                "required_secrets": plan.required_secrets,
            },
        )


# --------------------------------------------------------------------- pure helpers


def _all_deps(pkg: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        section = pkg.get(key)
        if isinstance(section, dict):
            out |= set(section)
    return out


def _scripts(pkg: dict[str, Any]) -> dict[str, Any]:
    scripts = pkg.get("scripts")
    return scripts if isinstance(scripts, dict) else {}


def _env_example_paths(directory: str) -> list[str]:
    """The package's own ``.env.example`` plus the workspace-root one (the skeleton ships both)."""
    if not directory:
        return [".env.example"]
    return [f"{directory}/.env.example", ".env.example"]


def _confidence(plan: InfraPlan) -> str:
    if plan.fe is None and plan.be is None:
        return "low"  # nothing recognizable to deploy
    if plan.warnings:
        return "medium"
    return "high"


__all__ = [
    "BackendPlan",
    "DatabasePlan",
    "FrontendPlan",
    "INFRA_PLAN_KIND",
    "InfraAnalyzer",
    "InfraPlan",
    "TARGET_BE",
    "TARGET_DB",
    "TARGET_FE",
    "is_secret",
]
