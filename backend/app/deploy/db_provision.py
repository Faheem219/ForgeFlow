"""Per-project database provisioning + URI injection (phase-36, D8).

Every generated app gets an **isolated database**, and ForgeFlow injects its ``MONGODB_URI`` into
the app for both preview (phase-15) and prod (phase-37). Two modes:

- **Platform** — a per-project *database* (``Project.app_db_name``, a globally-unique
  ``forgeflow_app_<uuid>``) inside the shared app-data cluster. Distinct database per project is the
  strongest isolation available on a shared cluster (D8 design note); MongoDB creates it lazily on
  first write, so "provisioning" is composing the right URI — no admin API call needed. ForgeFlow
  *manages* these (may drop on delete).
- **BYO** — a user-supplied ``MONGODB_URI`` from the vault (phase-34). Used verbatim, validated, and
  **never lifecycle-managed** — ForgeFlow never creates or drops a database it doesn't own.

The composed URI is a **secret** (it can carry cluster credentials): it is returned only to the
env-injection call sites, never logged and never placed on a client response.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum

from app.core.config import get_config

# SystemError shadows the builtin (taxonomy name fixed by the plan); A004 suppressed on-line.
from app.core.errors import ForbiddenError, SystemError, UserError  # noqa: A004
from app.db.models import Project
from app.db.models.enums import CredentialKind, CredentialScope
from app.deploy.secrets import SecretVault

logger = logging.getLogger(__name__)

_MONGO_SCHEMES = ("mongodb://", "mongodb+srv://")

#: Hosts that exist only on the machine (or docker network) the control plane runs on. A deployed
#: function resolving any of these reaches *itself*, not the database.
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "::1", "host.docker.internal"})

#: Drops a database on a cluster. Injected so the destructive path is testable without a live one.
Dropper = Callable[[str, str], Awaitable[None]]


class DbMode(StrEnum):
    platform = "platform"  # per-project DB in the shared cluster; ForgeFlow-managed
    byo = "byo"  # user-supplied URI; never managed


@dataclass(frozen=True)
class DbInfo:
    """Secret-safe metadata about a project's database — never carries the URI."""

    mode: DbMode
    #: The database the app uses. Platform: the project's own ``app_db_name``. BYO: the db named in
    #: the URI (or a placeholder when the BYO URI pins no database).
    db_name: str
    #: True only for platform DBs, which ForgeFlow may create/drop. A BYO DB is never managed.
    managed: bool


def _validate_uri(uri: str) -> str:
    if not uri.startswith(_MONGO_SCHEMES):
        raise UserError("A MongoDB URI must start with mongodb:// or mongodb+srv://")
    return uri


def _db_name_from_uri(uri: str) -> str | None:
    """The database path segment of a Mongo URI, or ``None`` when none is pinned."""
    for scheme in _MONGO_SCHEMES:
        if uri.startswith(scheme):
            rest = uri[len(scheme) :]
            break
    else:
        return None
    rest = rest.split("?", 1)[0]  # drop the query string
    slash = rest.find("/")
    if slash == -1:
        return None
    db = rest[slash + 1 :]
    return db or None


def _hosts_of(uri: str) -> list[str]:
    """The hostnames in a Mongo URI's authority, credentials and ports stripped."""
    for scheme in _MONGO_SCHEMES:
        if uri.startswith(scheme):
            rest = uri[len(scheme) :]
            break
    else:
        return []
    authority = rest.split("/", 1)[0].split("?", 1)[0]
    if "@" in authority:  # drop user:pass@
        authority = authority.rsplit("@", 1)[1]
    hosts: list[str] = []
    for part in authority.split(","):
        host = part.strip()
        if host.startswith("["):  # bracketed IPv6
            host = host[1 : host.find("]")] if "]" in host else host[1:]
        elif ":" in host:
            host = host.rsplit(":", 1)[0]
        if host:
            hosts.append(host.lower())
    return hosts


def unreachable_from_internet(uri: str) -> str | None:
    """Why a **deployed** app could never reach ``uri``, or ``None`` if it looks routable.

    A deployed function runs in someone else's datacentre. Two shapes can never work from there and
    both are easy to end up with while developing on a laptop:

    - a loopback/host-local address — the function resolves it to *itself*;
    - a bare docker service name (``forgeflow-appdb``, no dot) — meaningful only on the compose
      network, and correct for the *sandbox* (see :meth:`get_sandbox_mongodb_uri`), which is exactly
      why the two settings are easy to confuse.

    Both produce the same misleading symptom: the backend boots cleanly and then every query dies
    with mongoose's *"buffering timed out after 10000ms"*, which reads as a code bug and sends the
    user into the repair loop chasing nothing. Cheap string inspection only — no DNS, no connection
    attempt — because this runs on the deploy path and must not add latency or a new failure mode.
    """
    hosts = _hosts_of(uri)
    if not hosts:
        return None
    for host in hosts:
        if host in _LOCAL_HOSTS:
            return f"{host} is local to this machine"
        if "." not in host:
            return f"{host} is a private hostname that only resolves on this docker network"
    return None


def db_name_for_test_stage(app_db_name: str) -> str:
    """The database the *test* stage uses — a sibling of the app's own, never the app's own.

    Generated backend suites routinely clear collections between cases, so a test run pointed at
    the database the preview is serving would quietly destroy whatever the user built up in their
    app. One database over costs nothing and cannot.
    """
    return f"{app_db_name}_test"


def _compose_uri(base: str, db_name: str) -> str:
    """Point ``base`` (a cluster URI, no database) at ``db_name``, preserving any query string."""
    base = base.strip()
    if not base.startswith(_MONGO_SCHEMES):
        raise SystemError("Configured app-DB cluster URI is not a valid MongoDB URI")
    head, sep, query = base.partition("?")
    scheme_end = head.find("://") + 3
    authority = head[scheme_end:]
    slash = authority.find("/")
    authority = authority if slash == -1 else authority[:slash]  # drop any existing db path
    composed = f"{head[:scheme_end]}{authority}/{db_name}"
    return f"{composed}{sep}{query}" if sep else composed


class DbProvisioner:
    """Resolves, provisions and (optionally) tears down a project's isolated database."""

    def __init__(self, vault: SecretVault | None = None, dropper: Dropper | None = None) -> None:
        self._vault = vault or SecretVault()
        self._dropper = dropper or _default_drop

    # -- mode / metadata --------------------------------------------------------------------

    async def _byo_uri(self, project: Project) -> str | None:
        """A BYO ``mongo_uri`` for the project owner, if any. Fail-soft (→ platform on error)."""
        try:
            return await self._vault.get_credential(
                project.user_id, CredentialKind.mongo_uri, CredentialScope.byo
            )
        except Exception:  # DB/vault trouble must never take preview/deploy down
            logger.warning("BYO mongo lookup failed; using the platform database", exc_info=True)
            return None

    async def info(self, project: Project) -> DbInfo:
        """Secret-safe metadata (mode / db name / managed) — never the URI."""
        byo = await self._byo_uri(project)
        if byo:
            return DbInfo(
                mode=DbMode.byo,
                db_name=_db_name_from_uri(byo.strip()) or "(from BYO URI)",
                managed=False,
            )
        return DbInfo(mode=DbMode.platform, db_name=project.app_db_name, managed=True)

    # -- provisioning + injection -----------------------------------------------------------

    async def get_app_mongodb_uri(self, project: Project) -> str:
        """The app's ``MONGODB_URI`` (**secret** — inject into env only, never log/return).

        BYO wins and is used verbatim (validated); otherwise a per-project database in the shared
        cluster.
        """
        byo = await self._byo_uri(project)
        if byo:
            return _validate_uri(byo.strip())

        base = await self._platform_base()
        if not base:
            raise SystemError("No MongoDB cluster is configured for generated apps")
        return _compose_uri(base, project.app_db_name)

    async def assert_deployable(self, project: Project) -> None:
        """Refuse a **platform** database a deployed app could never reach.

        Called on the deploy path before any provider is touched, so a misconfigured
        ``APP_DB_CLUSTER_URI`` costs an error message rather than two billed deployments that come
        up and then time out. A **BYO** URI is exempt: the user supplied it, it is theirs to get
        right, and it may legitimately be reachable in ways this cannot see.
        """
        byo = await self._byo_uri(project)
        if byo:
            return
        base = await self._platform_base()
        if not base:
            return  # get_app_mongodb_uri raises the real error for this
        reason = unreachable_from_internet(base)
        if reason is not None:
            raise UserError(
                "The database this project would deploy against is not reachable from the "
                f"internet — {reason}. Set APP_DB_CLUSTER_URI to a publicly reachable cluster "
                "(e.g. MongoDB Atlas); the deployed backend runs on Vercel, not on this machine. "
                "APP_DB_SANDBOX_URI stays local — that one is for the sandbox preview."
            )

    async def get_sandbox_mongodb_uri(self, project: Project) -> str:
        """The app's ``MONGODB_URI`` **as a sandbox must address it** (secret — inject only).

        Same database, different route: a sandbox sits on an ``internal`` docker network, so a
        ``localhost`` cluster URI resolves to the sandbox itself and a host address has no route at
        all — the generated backend boots and then every query times out. ``APP_DB_SANDBOX_URI``
        names the same server the way containers can reach it. A BYO connection string wins as
        always, and a blank setting means "the normal URI is already reachable" (true when the
        control plane runs in docker too).
        """
        byo = await self._byo_uri(project)
        if byo:
            return _validate_uri(byo.strip())

        sandbox_base = str(get_config().get("app_db_sandbox_uri")).strip()
        if not sandbox_base:
            return await self.get_app_mongodb_uri(project)
        return _compose_uri(_validate_uri(sandbox_base), project.app_db_name)

    async def get_sandbox_test_mongodb_uri(self, project: Project) -> str:
        """The ``MONGODB_URI`` the **test stage** injects (secret — inject only).

        Same cluster and same sandbox route as :meth:`get_sandbox_mongodb_uri` — so a suite that
        genuinely talks to Mongo works instead of hanging until mongoose's buffering timeout — but
        pointed at :func:`db_name_for_test_stage`, one database over from the app's own. Without
        a URI at all the skeleton falls back to ``mongodb://localhost:27017/forgeflow_app``,
        which inside a sandbox is the sandbox itself, where nothing is listening.

        A BYO URI is redirected too. ForgeFlow never manages a BYO database, and that is exactly
        why it must not let a test run wipe one: the sibling is created lazily on first write and
        is the user's to drop.
        """
        byo = await self._byo_uri(project)
        if byo:
            uri = _validate_uri(byo.strip())
            named = _db_name_from_uri(uri) or project.app_db_name
            return _compose_uri(uri, db_name_for_test_stage(named))

        sandbox_base = str(get_config().get("app_db_sandbox_uri")).strip()
        if not sandbox_base:
            base = await self._platform_base()
            if not base:
                raise SystemError("No MongoDB cluster is configured for generated apps")
            return _compose_uri(base, db_name_for_test_stage(project.app_db_name))
        return _compose_uri(
            _validate_uri(sandbox_base), db_name_for_test_stage(project.app_db_name)
        )

    async def _platform_base(self) -> str:
        """Cluster base for platform DBs: vault override > APP_DB_CLUSTER_URI > MONGODB_URI."""
        override = await self._vault.platform_secret(CredentialKind.mongo_uri)
        if override and override.strip():
            return override.strip()
        cluster = str(get_config().get("app_db_cluster_uri")).strip()
        if cluster:
            return cluster
        return str(get_config().get("mongodb_uri")).strip()

    async def provision(self, project: Project) -> DbInfo:
        """Ensure the project has an isolated, reachable database target.

        Platform DBs are created lazily by MongoDB on first write, so this validates that a correct
        URI can be composed (and validates a BYO URI early) rather than calling any admin API.
        """
        await self.get_app_mongodb_uri(project)  # raises on a malformed BYO URI / missing cluster
        return await self.info(project)

    # -- isolation --------------------------------------------------------------------------

    def assert_owns_db(self, project: Project, db_name: str) -> None:
        """Guard for the data browser (Epic 9): a project may only reach its own platform database.

        Platform isolation is by distinct database name, so any request must target
        ``project.app_db_name``. (BYO isolation is delegated to the user-scoped BYO URI itself.)
        """
        if db_name != project.app_db_name:
            raise ForbiddenError("A project may only access its own database")

    # -- lifecycle --------------------------------------------------------------------------

    async def teardown(self, project: Project, *, drop_data: bool = False) -> bool:
        """Drop a project's **platform** database. Returns ``True`` only when a drop happened.

        Irreversible (destroys data), so it requires ``drop_data=True``. A **BYO** database is never
        dropped — ForgeFlow does not manage databases it does not own.
        """
        info = await self.info(project)
        if info.mode is DbMode.byo:
            return False  # never drop a database ForgeFlow doesn't own
        if not drop_data:
            return False  # explicit, destructive opt-in required
        base = await self._platform_base()
        if not base:  # pragma: no cover - provision() would have failed earlier
            return False
        uri = _compose_uri(base, project.app_db_name)
        await self._dropper(uri, project.app_db_name)
        logger.info("dropped platform app database", extra={"db": project.app_db_name})
        return True


async def _default_drop(uri: str, db_name: str) -> None:  # pragma: no cover - needs a live cluster
    from typing import Any

    from motor.motor_asyncio import AsyncIOMotorClient

    client: AsyncIOMotorClient[dict[str, Any]] = AsyncIOMotorClient(uri)
    try:
        await client.drop_database(db_name)
    finally:
        client.close()


__all__ = ["DbInfo", "DbMode", "DbProvisioner"]
