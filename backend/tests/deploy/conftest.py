"""Fixtures for the infra analyzer (phase-33) and the credential vault (phase-34).

The analyzer's only seam is a read-only workspace, so each shape (FE-only, FE+BE, FE+BE+DB) is just
a dict of files — deterministic, and no Docker anywhere. The vault needs a real ``FERNET_KEY``, so
each test that touches it gets a freshly generated one.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest
from beanie import PydanticObjectId
from cryptography.fernet import Fernet

from app.core.config import reset_config
from app.core.errors import NotFoundError, ProviderError
from app.db.models import Project
from app.deploy.secrets import reset_vault
from app.sandbox.schemas import FileContent, FileNode


@pytest.fixture
def fernet_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Install a valid, per-test encryption key (never a fixed one — keys are secrets)."""
    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("FERNET_KEY", key)
    reset_config()
    reset_vault()
    yield key
    reset_vault()


#: An Atlas-shaped cluster URI. Not a real cluster — nothing connects to it — but publicly routable
#: in shape, which is what the deploy path now insists on (phase-62).
DEPLOYABLE_CLUSTER_URI = "mongodb+srv://user:pw@cluster0.abcde.mongodb.net"


@pytest.fixture
def deployable_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A cluster URI a *deployed* app could actually reach (phase-62).

    The suite's own Mongo is on localhost, and since phase-62 the orchestrator refuses to deploy
    against a database that only resolves on this machine — a deployed function given a loopback
    address boots and then times out every query. Any test that runs a real deploy therefore has to
    say which cluster the deployed app would use, exactly as a real operator does.
    """
    monkeypatch.setenv("APP_DB_CLUSTER_URI", DEPLOYABLE_CLUSTER_URI)
    reset_config()
    yield
    reset_config()


@pytest.fixture
def blob_env(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> Iterator[None]:
    """Filesystem blobs so deploy logs never touch the shared meta DB (phase-37)."""
    monkeypatch.setenv("BLOB_BACKEND", "filesystem")
    monkeypatch.setenv("BLOB_FS_DIR", str(tmp_path))
    reset_config()
    yield


# --------------------------------------------------------------------- deploy adapters (phase-35)

Route = tuple[int, Any] | Callable[[httpx.Request], httpx.Response]


class FakeDeployApi:
    """A recording ``httpx.MockTransport``.

    Mocking the *transport* rather than a client keeps the tests honest: the adapter's real URLs,
    HTTP methods, auth header and JSON bodies all have to be right for a route to match.
    """

    def __init__(self, routes: dict[tuple[str, str], Route] | None = None) -> None:
        self.routes: dict[tuple[str, str], Route] = dict(routes or {})
        self.requests: list[httpx.Request] = []
        self.bodies: list[Any] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def route(self, method: str, path: str, status: int = 200, payload: Any = None) -> None:
        self.routes[(method, path)] = (status, payload)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body: Any = None
        if request.content:
            try:
                body = json.loads(request.content)
            except ValueError:
                body = request.content.decode("utf-8", "replace")
        self.bodies.append(body)

        # Longest path first so `/v1/services/{id}/deploys` wins over `/v1/services`.
        for method, path in sorted(self.routes, key=lambda k: len(k[1]), reverse=True):
            if request.method == method and request.url.path.startswith(path):
                route = self.routes[(method, path)]
                if callable(route):
                    return route(request)
                status, payload = route
                return httpx.Response(status, json=payload)
        return httpx.Response(404, json={"error": f"unrouted {request.method} {request.url.path}"})

    # -- assertions helpers ------------------------------------------------------------------

    def calls(self, method: str, contains: str = "") -> list[httpx.Request]:
        return [r for r in self.requests if r.method == method and contains in str(r.url)]

    def body_of(self, method: str, contains: str = "") -> Any:
        for request, body in zip(self.requests, self.bodies, strict=False):
            if request.method == method and contains in str(request.url):
                return body
        return None

    @property
    def paths(self) -> list[str]:
        return [r.url.path for r in self.requests]


async def no_sleep(_seconds: float) -> None:
    """Retry backoff stub — keeps retry tests instant."""
    return None


def error_detail(exc: ProviderError) -> dict[str, Any]:
    """The classified ``{provider, kind, target}`` payload off a deploy error, typed for asserts."""
    assert isinstance(exc.detail, dict)
    return exc.detail


class FakeAnalyzerWorkspace:
    """Serves a fixed file map; ``tree`` lists the files under a prefix."""

    def __init__(self, files: dict[str, str]) -> None:
        self.files = dict(files)
        self.reads: list[str] = []

    async def read(self, project: Project, path: str) -> FileContent:
        self.reads.append(path)
        if path not in self.files:
            raise NotFoundError(f"File not found: {path}")
        content = self.files[path]
        return FileContent(path=path, content=content, size=len(content.encode("utf-8")))

    async def tree(
        self, project: Project, path: str = ".", depth: int | None = None
    ) -> list[FileNode]:
        prefix = "" if path in (".", "") else path.rstrip("/") + "/"
        matches = [p for p in sorted(self.files) if p.startswith(prefix)]
        if not matches:
            raise NotFoundError(f"Not found: {path}")
        return [
            FileNode(path=p, type="file", size=len(self.files[p].encode("utf-8"))) for p in matches
        ]


async def make_project(name: str = "app") -> Project:
    return await Project(user_id=PydanticObjectId(), name=name, app_db_name="db").insert()


# --------------------------------------------------------------------- workspace shapes


def fe_package(*, with_build: bool = True) -> str:
    return json.dumps(
        {
            "name": "frontend",
            "scripts": {"dev": "vite", **({"build": "vite build"} if with_build else {})},
            "dependencies": {"react": "^18.3.1", "react-dom": "^18.3.1"},
            "devDependencies": {"vite": "^5.4.8", "typescript": "^5.6.2"},
        }
    )


def be_package(*, mongoose: bool = True, start: bool = True, build: bool = True) -> str:
    scripts: dict[str, str] = {"dev": "tsx watch src/index.ts"}
    if start:
        scripts["start"] = "node dist/index.js"
    if build:
        scripts["build"] = "tsc -p tsconfig.build.json"
    deps = {"express": "^4.21.0", "zod": "^3.23.8"}
    if mongoose:
        deps["mongoose"] = "^8.7.0"
    return json.dumps({"name": "backend", "scripts": scripts, "dependencies": deps})


FE_API_CLIENT = """
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:3001'
export const api = { base: API_BASE_URL }
"""

BE_CONFIG = """
export function loadConfig() {
  return {
    port: Number(process.env.PORT ?? 3001),
    mongoUri: process.env.MONGODB_URI ?? 'mongodb://localhost:27017/app',
    nodeEnv: process.env.NODE_ENV ?? 'development',
  }
}
"""


def fe_only_files() -> dict[str, str]:
    """A single-package Vite SPA at the workspace root — no backend, no database."""
    return {
        "package.json": fe_package(),
        "vite.config.ts": "export default defineConfig({ plugins: [react()] })",
        ".env.example": "VITE_API_BASE_URL=http://localhost:3001\n",
        "src/lib/api.ts": FE_API_CLIENT,
    }


def fe_be_files(*, mongoose: bool = False) -> dict[str, str]:
    """The fixed-stack skeleton layout (phase-22): frontend/ + backend/."""
    return {
        "package.json": json.dumps({"name": "forgeflow-app", "private": True}),
        ".env.example": "PORT=3001\nMONGODB_URI=mongodb://localhost:27017/app\nNODE_ENV=development\nVITE_API_BASE_URL=http://localhost:3001\n",
        "frontend/package.json": fe_package(),
        "frontend/vite.config.ts": "export default defineConfig({ plugins: [react()] })",
        "frontend/.env.example": "VITE_API_BASE_URL=http://localhost:3001\n",
        "frontend/src/lib/api.ts": FE_API_CLIENT,
        "backend/package.json": be_package(mongoose=mongoose),
        "backend/.env.example": "PORT=3001\nNODE_ENV=development\n",
        "backend/src/config.ts": BE_CONFIG,
    }


@pytest.fixture
def fe_only() -> FakeAnalyzerWorkspace:
    return FakeAnalyzerWorkspace(fe_only_files())


@pytest.fixture
def fe_be() -> FakeAnalyzerWorkspace:
    files = fe_be_files(mongoose=False)
    # No database: drop the Mongo references entirely.
    files["backend/src/config.ts"] = BE_CONFIG.replace(
        "    mongoUri: process.env.MONGODB_URI ?? 'mongodb://localhost:27017/app',\n", ""
    )
    files[".env.example"] = (
        "PORT=3001\nNODE_ENV=development\nVITE_API_BASE_URL=http://localhost:3001\n"
    )
    return FakeAnalyzerWorkspace(files)


@pytest.fixture
def fe_be_db() -> FakeAnalyzerWorkspace:
    return FakeAnalyzerWorkspace(fe_be_files(mongoose=True))
