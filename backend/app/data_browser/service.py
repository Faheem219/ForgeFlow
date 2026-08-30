"""Project-scoped data access (phase-41, D8) — CRUD over *one* project's database.

The whole security model is one rule: **the database is always derived from the authenticated
project, never from client input.** There is deliberately no parameter anywhere in this module that
takes a database name. A caller can name a collection and a document; it cannot name a database, so
there is no reachable path from a request to another project's data or to ``forgeflow_meta``.

Two guards back that up rather than trusting it:

1. the resolved database is checked against the project (``DbProvisioner.assert_owns_db``) for
   platform DBs, and
2. the control-plane database is refused outright, whatever the configuration says — in local dev
   the app cluster and the meta cluster are the same server, so this is a real boundary, not a
   theoretical one.

Queries themselves are constrained by :mod:`app.data_browser.guards` (operator allowlist, capped
depth/size/page/time). BYO databases work too: the URI the user supplied grants exactly what it
grants, and ForgeFlow adds no privileges of its own.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId

from app.core.config import get_config
from app.core.errors import ForbiddenError, NotFoundError, UserError
from app.data_browser.guards import (
    QUERY_MAX_TIME_MS,
    clamp_page,
    sanitize_filter,
    sanitize_sort,
    validate_collection,
)
from app.db.models import Project
from app.deploy.db_provision import DbInfo, DbMode, DbProvisioner

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CollectionInfo:
    name: str
    count: int


@dataclass(frozen=True)
class DocumentPage:
    documents: list[dict[str, Any]]
    total: int
    page: int
    limit: int

    @property
    def pages(self) -> int:
        return max(1, -(-self.total // self.limit))  # ceil


def jsonable(value: Any) -> Any:
    """Convert BSON to JSON-safe values (``ObjectId`` → str, datetimes → ISO)."""
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [jsonable(v) for v in value]
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def jsonable_doc(doc: dict[str, Any]) -> dict[str, Any]:
    """``jsonable`` for a document, keeping the precise return type at the call sites."""
    converted: dict[str, Any] = jsonable(doc)
    return converted


def _doc_id_query(doc_id: str) -> dict[str, Any]:
    """Match by ``ObjectId`` when the id looks like one, else by the raw ``_id`` value.

    Generated apps are free to use string or numeric ``_id``s, so the browser must handle both.
    """
    try:
        return {"_id": ObjectId(doc_id)}
    except (InvalidId, TypeError, ValueError):
        return {"_id": doc_id}


def _strip_immutable(doc: dict[str, Any]) -> dict[str, Any]:
    """``_id`` is immutable in MongoDB; silently dropping it makes round-tripping a doc work."""
    return {k: v for k, v in doc.items() if k != "_id"}


class DataBrowserService:
    """CRUD scoped to a single project's database. Never accepts a database name."""

    def __init__(
        self, provisioner: DbProvisioner | None = None, client_factory: Any | None = None
    ) -> None:
        self._provisioner = provisioner or DbProvisioner()
        self._client_factory = client_factory

    # -- connection (the isolation boundary) -------------------------------------------

    def _make_client(self, uri: str) -> Any:
        if self._client_factory is not None:
            return self._client_factory(uri)
        from motor.motor_asyncio import AsyncIOMotorClient

        # `tz_aware=True` for the same reason as the meta client (app/db/mongo.py): a naive
        # datetime serializes with no offset and the browser reads it as local time.
        client: Any = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000, tz_aware=True)
        return client

    async def _open(self, project: Project) -> tuple[Any, Any]:
        """Open the project's own database. The only place a DB handle is produced.

        The URI is a secret (it can carry cluster credentials): it is used to build the client and
        never returned, logged, or attached to a response.
        """
        uri = await self._provisioner.get_app_mongodb_uri(project)
        info = await self._provisioner.info(project)
        client = self._make_client(uri)

        db = client.get_default_database()
        if db is None:  # a BYO URI that pins no database
            if info.mode is DbMode.platform:  # pragma: no cover - platform URIs always pin one
                db = client[project.app_db_name]
            else:
                client.close()
                raise UserError(
                    "Your MongoDB URI does not name a database — add one, e.g. "
                    "mongodb+srv://…/mydb"
                )

        self._assert_isolated(project, db.name, info)
        return client, db

    def _assert_isolated(self, project: Project, db_name: str, info: DbInfo) -> None:
        """Refuse anything that is not this project's own data — belt and braces."""
        meta_db = str(get_config().get("forgeflow_meta_db"))
        if db_name == meta_db:
            # Reachable only via misconfiguration, which is exactly when a guard earns its keep.
            logger.error("data browser resolved the control-plane database; refusing")
            raise ForbiddenError("The control-plane database is not browsable")
        if info.mode is DbMode.platform:
            self._provisioner.assert_owns_db(project, db_name)

    # -- reads --------------------------------------------------------------------------

    async def db_info(self, project: Project) -> DbInfo:
        """Secret-safe description of where this project's data lives (never the URI)."""
        return await self._provisioner.info(project)

    async def list_collections(self, project: Project) -> list[CollectionInfo]:
        client, db = await self._open(project)
        try:
            names = sorted(
                name
                for name in await db.list_collection_names()
                if not name.startswith("system.")  # never expose MongoDB's internals
            )
            return [
                CollectionInfo(name=name, count=await db[name].estimated_document_count())
                for name in names
            ]
        finally:
            client.close()

    async def find(
        self,
        project: Project,
        collection: str,
        *,
        filter: Any = None,  # noqa: A002 - mirrors the Mongo/driver vocabulary
        sort: Any = None,
        page: Any = 1,
        limit: Any = None,
    ) -> DocumentPage:
        name = validate_collection(collection)
        query = sanitize_filter(filter)
        order = sanitize_sort(sort)
        page_num, size = clamp_page(page, limit)

        client, db = await self._open(project)
        try:
            cursor = db[name].find(query).skip((page_num - 1) * size).limit(size)
            cursor = cursor.max_time_ms(QUERY_MAX_TIME_MS)
            if order:
                cursor = cursor.sort(order)
            documents = [jsonable_doc(doc) async for doc in cursor]
            total = await db[name].count_documents(query, maxTimeMS=QUERY_MAX_TIME_MS)
            return DocumentPage(documents=documents, total=total, page=page_num, limit=size)
        finally:
            client.close()

    async def get(self, project: Project, collection: str, doc_id: str) -> dict[str, Any]:
        name = validate_collection(collection)
        client, db = await self._open(project)
        try:
            doc = await db[name].find_one(_doc_id_query(doc_id))
            if doc is None:
                raise NotFoundError("Document not found")
            return jsonable_doc(doc)
        finally:
            client.close()

    async def count(
        self,
        project: Project,
        collection: str,
        filter: Any = None,  # noqa: A002 - mirrors the Mongo/driver vocabulary
    ) -> int:
        name = validate_collection(collection)
        query = sanitize_filter(filter)
        client, db = await self._open(project)
        try:
            count: int = await db[name].count_documents(query, maxTimeMS=QUERY_MAX_TIME_MS)
            return count
        finally:
            client.close()

    # -- writes -------------------------------------------------------------------------

    async def insert(
        self, project: Project, collection: str, document: dict[str, Any]
    ) -> dict[str, Any]:
        name = validate_collection(collection)
        if not isinstance(document, dict) or not document:
            raise UserError("A document object is required")

        client, db = await self._open(project)
        try:
            result = await db[name].insert_one(dict(document))
            created = await db[name].find_one({"_id": result.inserted_id})
            return jsonable_doc(created or {"_id": result.inserted_id})
        finally:
            client.close()

    async def update(
        self, project: Project, collection: str, doc_id: str, document: dict[str, Any]
    ) -> dict[str, Any]:
        """Replace a document's fields. ``_id`` is immutable and is ignored if supplied."""
        name = validate_collection(collection)
        if not isinstance(document, dict):
            raise UserError("A document object is required")

        client, db = await self._open(project)
        try:
            query = _doc_id_query(doc_id)
            result = await db[name].update_one(query, {"$set": _strip_immutable(document)})
            if result.matched_count == 0:
                raise NotFoundError("Document not found")
            updated = await db[name].find_one(query)
            return jsonable_doc(updated or {})
        finally:
            client.close()

    async def delete(self, project: Project, collection: str, doc_id: str) -> bool:
        """Delete a document. Irreversible — the API layer requires an explicit call."""
        name = validate_collection(collection)
        client, db = await self._open(project)
        try:
            result = await db[name].delete_one(_doc_id_query(doc_id))
            if result.deleted_count == 0:
                raise NotFoundError("Document not found")
            return True
        finally:
            client.close()


__all__ = ["CollectionInfo", "DataBrowserService", "DocumentPage", "jsonable", "jsonable_doc"]
