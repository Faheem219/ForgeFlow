"""Cost API (phase-46): spend is visible, correct, and scoped to who may see it.

Two things are being defended. **Correctness** — the dashboard must sum the same `Run` records the
budget guard sums, or a user is told they have headroom they do not have. **Scope** — one user's
spend is not another's business, and global spend is an admin's view only.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from beanie import PydanticObjectId
from httpx import ASGITransport, AsyncClient

from app.api.app import create_app
from app.core.config import reset_config
from app.db.models import Run
from app.db.models.common import utcnow
from app.db.models.enums import UserRole
from app.db.models.user import User

pytestmark = pytest.mark.usefixtures("mongo_db")


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


async def _auth(client: AsyncClient, email: str) -> dict[str, str]:
    reg = await client.post("/auth/register", json={"email": email, "password": "password123"})
    return {"Authorization": f"Bearer {reg.json()['access_token']}"}


async def _project(client: AsyncClient, headers: dict[str, str], name: str = "p") -> str:
    created = await client.post("/projects", json={"name": name}, headers=headers)
    return str(created.json()["id"])


async def _run(
    project_id: str, kind: str, tokens: int, inr: float, *, outcome: str | None = "ok"
) -> Run:
    run = Run(project_id=PydanticObjectId(project_id), kind=kind, outcome=outcome)
    run.cost.tokens = tokens
    run.cost.inr = inr
    if outcome is not None:
        run.finished_at = utcnow()
    return await run.insert()


# --------------------------------------------------------------------- per-project cost


async def test_project_cost_sums_every_run(client: AsyncClient) -> None:
    headers = await _auth(client, "spend@cost.test")
    pid = await _project(client, headers)
    await _run(pid, "codegen:build", 1000, 2.5)
    await _run(pid, "repair:loop", 500, 1.25)

    body = (await client.get(f"/projects/{pid}/cost", headers=headers)).json()

    assert body["tokens"] == 1500
    assert body["inr"] == 3.75
    assert body["runs"] == 2


async def test_cost_is_broken_down_by_the_kind_of_work(client: AsyncClient) -> None:
    """ "Where did the money go" is the question a cost panel has to answer."""
    headers = await _auth(client, "bykind@cost.test")
    pid = await _project(client, headers)
    await _run(pid, "codegen:build", 1000, 2.0)
    await _run(pid, "codegen:build", 500, 1.0)
    await _run(pid, "repair:loop", 250, 0.5)

    by_kind = (await client.get(f"/projects/{pid}/cost", headers=headers)).json()["by_kind"]

    assert by_kind["codegen:build"] == {"runs": 2, "tokens": 1500, "inr": 3.0}
    assert by_kind["repair:loop"]["runs"] == 1


async def test_a_project_with_no_runs_reports_zero_not_an_error(client: AsyncClient) -> None:
    headers = await _auth(client, "empty@cost.test")
    pid = await _project(client, headers)

    body = (await client.get(f"/projects/{pid}/cost", headers=headers)).json()

    assert body["inr"] == 0 and body["runs"] == 0 and body["by_kind"] == {}


async def test_headroom_is_unknown_rather_than_zero_when_no_cap_is_set(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An uncapped project has *unlimited* headroom — reporting 0 would read as "stop"."""
    monkeypatch.delenv("BUDGET_CAP_INR_PER_PROJECT", raising=False)
    reset_config()
    headers = await _auth(client, "nocap@cost.test")
    pid = await _project(client, headers)
    await _run(pid, "codegen:build", 1000, 5.0)

    budget = (await client.get(f"/projects/{pid}/cost", headers=headers)).json()["project"]

    assert budget["cap_inr"] is None
    assert budget["headroom_inr"] is None
    assert budget["used_ratio"] is None
    assert budget["warning"] is False and budget["halted"] is False


async def test_headroom_and_ratio_are_reported_against_a_cap(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BUDGET_CAP_INR_PER_PROJECT", "100")
    reset_config()
    headers = await _auth(client, "cap@cost.test")
    pid = await _project(client, headers)
    await _run(pid, "codegen:build", 1000, 40.0)

    budget = (await client.get(f"/projects/{pid}/cost", headers=headers)).json()["project"]

    assert budget["cap_inr"] == 100
    assert budget["spent_inr"] == 40
    assert budget["headroom_inr"] == 60
    assert budget["used_ratio"] == 0.4
    assert budget["warning"] is False


async def test_approaching_the_cap_raises_a_warning_before_the_halt(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hitting a cap with no prior signal is the surprise this phase removes."""
    monkeypatch.setenv("BUDGET_CAP_INR_PER_PROJECT", "100")
    monkeypatch.setenv("BUDGET_WARN_RATIO", "0.8")
    reset_config()
    headers = await _auth(client, "warn@cost.test")
    pid = await _project(client, headers)
    await _run(pid, "codegen:build", 1000, 85.0)

    budget = (await client.get(f"/projects/{pid}/cost", headers=headers)).json()["project"]

    assert budget["warning"] is True
    assert budget["halted"] is False
    assert budget["headroom_inr"] == 15


async def test_reaching_the_cap_reports_halted_not_merely_warning(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BUDGET_CAP_INR_PER_PROJECT", "100")
    reset_config()
    headers = await _auth(client, "halt@cost.test")
    pid = await _project(client, headers)
    await _run(pid, "codegen:build", 1000, 120.0)

    budget = (await client.get(f"/projects/{pid}/cost", headers=headers)).json()["project"]

    assert budget["halted"] is True
    assert budget["warning"] is False  # past warning — the louder signal wins
    assert budget["headroom_inr"] == -20  # honestly negative, not clamped


# --------------------------------------------------------------------- run explorer


async def test_the_run_explorer_lists_runs_newest_first_with_duration(
    client: AsyncClient,
) -> None:
    headers = await _auth(client, "runs@cost.test")
    pid = await _project(client, headers)
    await _run(pid, "codegen:build", 1000, 2.0)
    await _run(pid, "repair:loop", 500, 1.0)

    runs = (await client.get(f"/projects/{pid}/runs", headers=headers)).json()

    assert [r["kind"] for r in runs] == ["repair:loop", "codegen:build"]
    assert runs[0]["tokens"] == 500 and runs[0]["outcome"] == "ok"
    assert runs[0]["duration_s"] is not None and runs[0]["duration_s"] >= 0


async def test_an_open_run_reports_no_duration_rather_than_zero(client: AsyncClient) -> None:
    headers = await _auth(client, "open@cost.test")
    pid = await _project(client, headers)
    await _run(pid, "codegen:build", 0, 0.0, outcome=None)

    runs = (await client.get(f"/projects/{pid}/runs", headers=headers)).json()

    assert runs[0]["finished_at"] is None
    assert runs[0]["duration_s"] is None


# --------------------------------------------------------------------- scope


async def test_another_users_project_cost_is_not_readable(client: AsyncClient) -> None:
    owner = await _auth(client, "owner@cost.test")
    pid = await _project(client, owner)
    await _run(pid, "codegen:build", 1000, 9.0)

    intruder = await _auth(client, "intruder@cost.test")

    for path in (f"/projects/{pid}/cost", f"/projects/{pid}/runs"):
        assert (await client.get(path, headers=intruder)).status_code == 404, path


async def test_global_spend_requires_an_admin(client: AsyncClient) -> None:
    headers = await _auth(client, "plain@cost.test")
    assert (await client.get("/admin/cost", headers=headers)).status_code == 403
    assert (await client.get("/admin/cost")).status_code == 401


async def test_an_admin_sees_platform_wide_spend(client: AsyncClient) -> None:
    a_headers = await _auth(client, "usera@cost.test")
    pid_a = await _project(client, a_headers, "alpha")
    await _run(pid_a, "codegen:build", 1000, 10.0)

    b_headers = await _auth(client, "userb@cost.test")
    pid_b = await _project(client, b_headers, "beta")
    await _run(pid_b, "codegen:build", 500, 4.0)

    admin_headers = await _auth(client, "admin@cost.test")
    admin = await User.find_one({"email": "admin@cost.test"})
    assert admin is not None
    admin.role = UserRole.admin
    await admin.save()

    body = (await client.get("/admin/cost", headers=admin_headers)).json()

    assert body["inr"] == 14.0
    assert body["tokens"] == 1500
    assert body["projects"] == 2
    # Ranked by spend, so the expensive project is the first thing an admin sees.
    assert body["top_projects"][0]["name"] == "alpha"
    assert body["top_projects"][0]["inr"] == 10.0
