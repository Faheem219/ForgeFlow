"""Artifact service (phase-07): versioned, never-overwritten stage outputs.

Wraps :class:`app.db.repos.ArtifactRepo` (which owns per-``(project, stage, type)`` version
numbering) and adds the payload strategy + text diffing the plan asks for:

- **Small text/structured payloads** stay inline in ``Artifact.meta`` (key ``content``).
- **Large payloads** (> ``artifact_inline_max_bytes``) go to the :class:`BlobStore`; the doc keeps
  only a ``ref``. Either way :meth:`get_content` resolves the text transparently.

Nothing is ever overwritten — each call to :meth:`create_version` appends a new version.
"""

from __future__ import annotations

import difflib

from beanie import PydanticObjectId

from app.core.config import get_config
from app.db.blobs import BlobStore, get_blob_store
from app.db.models import Artifact
from app.db.models.enums import ArtifactType, Stage
from app.db.repos import ArtifactRepo

_INLINE_KEY = "content"


class ArtifactService:
    def __init__(
        self, blob_store: BlobStore | None = None, inline_max_bytes: int | None = None
    ) -> None:
        self._artifacts = ArtifactRepo()
        self._blobs = blob_store if blob_store is not None else get_blob_store()
        self._inline_max_bytes = inline_max_bytes

    def _threshold(self) -> int:
        if self._inline_max_bytes is not None:
            return self._inline_max_bytes
        return int(get_config().get("artifact_inline_max_bytes"))

    async def create_version(
        self,
        project_id: PydanticObjectId,
        stage: Stage,
        artifact_type: ArtifactType,
        *,
        text: str | None = None,
        meta: dict[str, object] | None = None,
    ) -> Artifact:
        """Append the next version of ``(project, stage, type)``.

        ``text`` is the artifact payload (design HTML, a diff, stdout, …). Small payloads are
        stored inline; large ones are offloaded to the blob store and referenced by ``ref``.
        ``meta`` holds structured fields; it is never used for the ``content`` key.
        """
        doc_meta: dict[str, object] = dict(meta or {})
        ref: str | None = None

        if text is not None:
            data = text.encode("utf-8")
            if len(data) > self._threshold():
                ref = await self._blobs.put(data)
            else:
                doc_meta[_INLINE_KEY] = text

        return await self._artifacts.create_version(
            project_id, stage, artifact_type, ref=ref, meta=doc_meta
        )

    async def get_latest(
        self, project_id: PydanticObjectId, stage: Stage, artifact_type: ArtifactType
    ) -> Artifact | None:
        return await self._artifacts.latest_version(project_id, stage, artifact_type)

    async def get_latest_of_kind(
        self,
        project_id: PydanticObjectId,
        stage: Stage,
        artifact_type: ArtifactType,
        kind: str,
    ) -> Artifact | None:
        """The newest version of ``(project, stage, type)`` whose ``meta['kind']`` matches ``kind``.

        :meth:`get_latest` returns the newest artifact of a ``(stage, type)`` pair regardless of its
        kind. That is wrong when two kinds share a pair — phase-56 stores a ``build_plan`` under the
        same ``(build, code_change)`` pair as the ``build_report``, so "the newest report" must
        filter by kind or a plan write silently hides the last report from incremental resume.
        """
        versions = await self.list_versions(project_id, stage, artifact_type)
        for artifact in reversed(versions):  # list_versions is ascending; newest matching first
            if artifact.meta.get("kind") == kind:
                return artifact
        return None

    async def get_version(
        self,
        project_id: PydanticObjectId,
        stage: Stage,
        artifact_type: ArtifactType,
        version: int,
    ) -> Artifact | None:
        return await self._artifacts.get_version(project_id, stage, artifact_type, version)

    async def list_versions(
        self, project_id: PydanticObjectId, stage: Stage, artifact_type: ArtifactType
    ) -> list[Artifact]:
        return await self._artifacts.list_versions(project_id, stage, artifact_type)

    async def get_by_id(self, artifact_id: PydanticObjectId) -> Artifact | None:
        return await self._artifacts.get(artifact_id)

    async def get_content(self, artifact: Artifact) -> str | None:
        """Resolve an artifact's text payload — from the blob store or inline — or ``None``."""
        if artifact.ref is not None:
            return (await self._blobs.get(artifact.ref)).decode("utf-8")
        inline = artifact.meta.get(_INLINE_KEY)
        return inline if isinstance(inline, str) else None

    async def diff(self, a: Artifact, b: Artifact) -> str:
        """Unified diff of two text artifacts (``a`` → ``b``)."""
        left = await self.get_content(a) or ""
        right = await self.get_content(b) or ""
        diff = difflib.unified_diff(
            left.splitlines(keepends=True),
            right.splitlines(keepends=True),
            fromfile=f"{a.type}@v{a.version}",
            tofile=f"{b.type}@v{b.version}",
        )
        return "".join(diff)

    async def list_for_project(
        self,
        project_id: PydanticObjectId,
        *,
        stage: Stage | None = None,
        artifact_type: ArtifactType | None = None,
    ) -> list[Artifact]:
        query: dict[str, object] = {"project_id": project_id}
        if stage is not None:
            query["stage"] = stage
        if artifact_type is not None:
            query["type"] = artifact_type
        return await Artifact.find(query).sort("+created_at").to_list()

    async def delete_for_project(self, project_id: PydanticObjectId) -> None:
        """Cascade helper: delete every artifact of a project and its offloaded blobs."""
        artifacts = await Artifact.find({"project_id": project_id}).to_list()
        for artifact in artifacts:
            if artifact.ref is not None:
                await self._blobs.delete(artifact.ref)
            await artifact.delete()
