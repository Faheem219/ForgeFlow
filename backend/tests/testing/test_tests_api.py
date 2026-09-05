"""Testing API (phase-28): run endpoint wiring + structured reads + ownership.

The run endpoint is exercised with the ``TestRunner`` swapped for a fake (the real runner needs a
sandbox); the read endpoints run against directly-inserted ``TestRun`` docs.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from beanie import PydanticObjectId
from httpx import ASGITransport, AsyncClient

from app.api.app import create_app
from app.db.models import Project, TestRun
from app.db.models.enums import TestEnv
from app.testing.models import TestResult, TestStatus

pytestmark = pytest.mark.usefixtures("mongo_db")


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


async def _register(client: AsyncClient, email: str) -> str:
    resp = await client.post("/auth/register", json={"email": email, "password": "password123"})
    token: str = resp.json()["access_token"]
    return token


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _project(client: AsyncClient, token: str) -> str:
    resp = await client.post("/projects", json={"name": "p"}, headers=_auth(token))
    pid: str = resp.json()["id"]
    return pid


def _result(criterion: str, status: TestStatus) -> dict[str, object]:
    return TestResult(
        name=f"[{criterion}] t", status=status, framework="jest", criterion_id=criterion
    ).model_dump(mode="json")


async def _insert_run(
    project_id: str, *statuses: TestStatus, env: TestEnv = TestEnv.sandbox
) -> TestRun:
    results = [_result(f"ac-{i}", s) for i, s in enumerate(statuses)]
    failures = [r for r, s in zip(results, statuses, strict=True) if s is TestStatus.failed]
    return await TestRun(
        project_id=PydanticObjectId(project_id),
        results=results,
        failures=failures,
        env=env,
    ).insert()


class _FakeRunner:
    """Stand-in for TestRunner so the endpoint is testable without a sandbox."""

    last: dict[str, object] = {}

    async def run(
        self, project: Project, scope: str = "all", name_filter: str | None = None
    ) -> TestRun:
        _FakeRunner.last = {"scope": scope, "name_filter": name_filter}
        assert project.id is not None
        return await _insert_run(str(project.id), TestStatus.passed, TestStatus.failed)


async def test_run_endpoint_returns_structured_results(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.testing.router.TestRunner", _FakeRunner)
    token = await _register(client, "runner@example.com")
    pid = await _project(client, token)

    resp = await client.post(
        f"/projects/{pid}/tests/run",
        json={"scope": "unit", "filter": "todo"},
        headers=_auth(token),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2 and body["passed"] == 1 and body["failed"] == 1
    assert body["green"] is False
    assert len(body["failures"]) == 1 and body["failures"][0]["status"] == "failed"
    # The request scope + filter reached the runner.
    assert _FakeRunner.last == {"scope": "unit", "name_filter": "todo"}


async def test_list_and_get_runs(client: AsyncClient) -> None:
    token = await _register(client, "reader@example.com")
    pid = await _project(client, token)

    await _insert_run(pid, TestStatus.passed, TestStatus.passed)
    run2 = await _insert_run(pid, TestStatus.passed, TestStatus.failed)

    listed = await client.get(f"/projects/{pid}/tests/runs", headers=_auth(token))
    assert listed.status_code == 200
    assert len(listed.json()) == 2

    detail = await client.get(f"/projects/{pid}/tests/runs/{run2.id}", headers=_auth(token))
    assert detail.status_code == 200
    body = detail.json()
    assert body["failed"] == 1 and len(body["results"]) == 2


async def test_listing_is_scoped_to_the_sandbox_by_default(client: AsyncClient) -> None:
    """A live run must not masquerade as the Test stage's newest result.

    Live validation (phase-39) runs only the E2E subset against the deployed URL and persists it in
    the same collection. An unscoped listing put that run first, so the Test panel — which reads
    ``data[0]`` — showed the full suite collapsing to a handful of tests until it was re-run.
    """
    token = await _register(client, "envscope@example.com")
    pid = await _project(client, token)

    sandbox = await _insert_run(pid, TestStatus.passed, TestStatus.passed)
    # Newer than the sandbox run, but it belongs to Validate, not to the Test stage.
    live = await _insert_run(pid, TestStatus.passed, env=TestEnv.live)

    default = await client.get(f"/projects/{pid}/tests/runs", headers=_auth(token))
    assert default.status_code == 200
    assert [r["id"] for r in default.json()] == [str(sandbox.id)]

    only_live = await client.get(f"/projects/{pid}/tests/runs?env=live", headers=_auth(token))
    assert [r["id"] for r in only_live.json()] == [str(live.id)]

    every = await client.get(f"/projects/{pid}/tests/runs?env=all", headers=_auth(token))
    assert {r["id"] for r in every.json()} == {str(sandbox.id), str(live.id)}
    assert every.json()[0]["id"] == str(live.id)  # newest first, across environments

    bad = await client.get(f"/projects/{pid}/tests/runs?env=nope", headers=_auth(token))
    assert bad.status_code == 422


async def test_run_ownership_is_enforced(client: AsyncClient) -> None:
    owner = await _register(client, "owner@example.com")
    pid = await _project(client, owner)
    run = await _insert_run(pid, TestStatus.passed)

    intruder = await _register(client, "intruder@example.com")
    # A run under someone else's project is a 404 (existence never leaked).
    resp = await client.get(f"/projects/{pid}/tests/runs/{run.id}", headers=_auth(intruder))
    assert resp.status_code == 404


async def test_unknown_run_is_404(client: AsyncClient) -> None:
    token = await _register(client, "missing@example.com")
    pid = await _project(client, token)
    resp = await client.get(
        f"/projects/{pid}/tests/runs/{PydanticObjectId()}", headers=_auth(token)
    )
    assert resp.status_code == 404
