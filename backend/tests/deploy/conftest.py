"""Fixtures for the infra analyzer (phase-33).

The analyzer's only seam is a read-only workspace, so each shape (FE-only, FE+BE, FE+BE+DB) is just
a dict of files — deterministic, and no Docker anywhere.
"""

from __future__ import annotations

import json

import pytest
from beanie import PydanticObjectId

from app.core.errors import NotFoundError
from app.db.models import Project
from app.sandbox.schemas import FileContent, FileNode


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
