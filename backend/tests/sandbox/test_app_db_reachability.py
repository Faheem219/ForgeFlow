"""The generated app's database must be addressed the way a *sandbox* can reach it.

Observed in a real preview: the generated backend booted ("Server listening on 0.0.0.0:3001",
`/health` 200) and then every query died with

    MongooseError: Operation `todos.find()` buffering timed out after 10000ms

because it had been handed `mongodb://localhost:27017`. Inside the sandbox `localhost` is the
sandbox — and its network is `internal`, so there is no route to a database on the host either. The
generated code was blameless; the environment was wrong, and nothing said so.
"""

from __future__ import annotations

import pytest
from beanie import PydanticObjectId

from app.core.config import reset_config
from app.db.models import Project
from app.deploy.db_provision import DbProvisioner
from app.sandbox.preview import DB_UNREACHABLE_WARNING, loopback_db_host

pytestmark = pytest.mark.usefixtures("mongo_db")


async def _project() -> Project:
    return await Project(
        user_id=PydanticObjectId(), name="todo", app_db_name="forgeflow_app_abc"
    ).insert()


# ---------------------------------------------------------------- detecting the dead end


@pytest.mark.parametrize(
    ("uri", "expected"),
    [
        ("mongodb://localhost:27017/app", "localhost:27017"),
        ("mongodb://127.0.0.1:27017/app", "127.0.0.1:27017"),
        ("mongodb://localhost/app", "localhost"),
        ("mongodb://0.0.0.0:27017/app", "0.0.0.0:27017"),
        # Reachable from a container — must NOT be flagged.
        ("mongodb://forgeflow-appdb:27017/app", None),
        ("mongodb://mongo:27017/app", None),
        ("mongodb+srv://cluster0.abcd.mongodb.net/app", None),
        ("mongodb://10.5.0.3:27017/app", None),
    ],
)
def test_loopback_hosts_are_recognised(uri: str, expected: str | None) -> None:
    assert loopback_db_host(uri) == expected


def test_credentials_are_never_echoed_in_the_diagnosis() -> None:
    """A BYO connection string carries a password; the warning must not become a leak."""
    host = loopback_db_host("mongodb://admin:sup3rsecret@localhost:27017/app")

    assert host == "localhost:27017"
    warning = DB_UNREACHABLE_WARNING.format(host=host)
    assert "sup3rsecret" not in warning
    assert "admin" not in warning
    # …and it still says what to do.
    assert "APP_DB_CLUSTER_URI" in warning


# ---------------------------------------------------------------- choosing the right address


async def test_the_sandbox_gets_the_sandbox_address(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_DB_CLUSTER_URI", "mongodb://localhost:27018")
    monkeypatch.setenv("APP_DB_SANDBOX_URI", "mongodb://forgeflow-appdb:27017")
    reset_config()
    project = await _project()
    provisioner = DbProvisioner()

    control = await provisioner.get_app_mongodb_uri(project)
    sandbox = await provisioner.get_sandbox_mongodb_uri(project)

    # Same database, two routes: the host-published port for the control plane…
    assert control == "mongodb://localhost:27018/forgeflow_app_abc"
    # …and the container name for the sandbox, which cannot use the first.
    assert sandbox == "mongodb://forgeflow-appdb:27017/forgeflow_app_abc"
    assert loopback_db_host(sandbox) is None


async def test_a_blank_sandbox_uri_changes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Correct when the control plane runs in docker too — both see the same address."""
    monkeypatch.setenv("APP_DB_CLUSTER_URI", "mongodb://forgeflow-appdb:27017")
    monkeypatch.setenv("APP_DB_SANDBOX_URI", "")
    reset_config()
    project = await _project()
    provisioner = DbProvisioner()

    assert await provisioner.get_sandbox_mongodb_uri(
        project
    ) == await provisioner.get_app_mongodb_uri(project)


async def test_the_database_name_is_still_per_project(monkeypatch: pytest.MonkeyPatch) -> None:
    """D8's isolation is the database name — the sandbox route must not flatten it."""
    monkeypatch.setenv("APP_DB_SANDBOX_URI", "mongodb://forgeflow-appdb:27017")
    reset_config()
    first = await _project()
    second = await Project(
        user_id=PydanticObjectId(), name="other", app_db_name="forgeflow_app_xyz"
    ).insert()

    provisioner = DbProvisioner()
    one = await provisioner.get_sandbox_mongodb_uri(first)
    two = await provisioner.get_sandbox_mongodb_uri(second)

    assert one.endswith("/forgeflow_app_abc")
    assert two.endswith("/forgeflow_app_xyz")
    assert one != two
