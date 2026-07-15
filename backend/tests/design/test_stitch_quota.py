"""Stitch quota: fail-soft at the cap, degraded near it, design.quota events, no token leakage."""

from __future__ import annotations

import pytest

from app.core.config import reset_config
from app.core.errors import ProviderError
from app.design.base import ProviderHealth
from app.design.stitch import StitchDesignProvider
from app.design.stitch_auth import StitchAuth
from app.design.stitch_errors import StitchErrorKind
from app.design.stitch_quota import PLATFORM_CHANNEL, QuotaStatus, StitchQuota
from app.realtime.hub import get_hub
from app.realtime.schemas import EventType
from tests.design.test_stitch_mock import FakeStitchClient

pytestmark = pytest.mark.usefixtures("mongo_db")

_SAFE_EVENT_KEYS = {"provider", "window", "used", "limit", "remaining", "status"}


def _set(monkeypatch: pytest.MonkeyPatch, **env: str) -> None:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    reset_config()


def _with_creds(monkeypatch: pytest.MonkeyPatch, **extra: str) -> None:
    _set(
        monkeypatch,
        STITCH_CLIENT_ID="id",
        STITCH_CLIENT_SECRET="secret",
        STITCH_TOKEN_URL="https://stitch.example/token",
        STITCH_MCP_URL="https://stitch.example/mcp",
        **extra,
    )


async def test_record_usage_counts_and_exhausts(monkeypatch: pytest.MonkeyPatch) -> None:
    _set(monkeypatch, STITCH_QUOTA_MONTHLY="2")
    quota = StitchQuota()

    assert (await quota.record_usage()).used == 1
    snap = await quota.record_usage()
    assert snap.used == 2
    assert snap.status is QuotaStatus.exhausted
    assert snap.remaining == 0


async def test_check_available_fails_soft_at_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    _set(monkeypatch, STITCH_QUOTA_MONTHLY="1")
    quota = StitchQuota()
    await quota.record_usage()  # fill the single slot

    with pytest.raises(ProviderError) as excinfo:
        await quota.check_available()

    err = excinfo.value
    assert err.fallback_hint  # actionable: switch to figma/fake or wait
    assert isinstance(err.detail, dict) and err.detail["kind"] == StitchErrorKind.quota


async def test_degraded_when_approaching_the_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    _set(monkeypatch, STITCH_QUOTA_MONTHLY="10", STITCH_QUOTA_SOFT_RATIO="0.9")
    quota = StitchQuota()
    for _ in range(9):
        await quota.record_usage()

    snap = await quota.snapshot()
    assert snap.used == 9
    assert snap.status is QuotaStatus.degraded


async def test_quota_event_is_emitted_without_any_token(monkeypatch: pytest.MonkeyPatch) -> None:
    _set(monkeypatch, STITCH_QUOTA_MONTHLY="1")
    hub = get_hub()

    async with hub.subscription(PLATFORM_CHANNEL) as queue:
        await StitchQuota().record_usage()
        event = await queue.get()

    assert event.event == str(EventType.design_quota)
    assert event.payload["status"] == str(QuotaStatus.exhausted)
    # Payload is strictly the safe, token-free shape.
    assert set(event.payload) == _SAFE_EVENT_KEYS


async def test_provider_generation_blocked_when_quota_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _with_creds(monkeypatch, STITCH_QUOTA_MONTHLY="1")
    await StitchQuota().record_usage()  # exhaust it

    async def fetch_token() -> tuple[str, float]:
        return "access-xyz", 3600.0

    client = FakeStitchClient()
    provider = StitchDesignProvider(client=client, auth=StitchAuth(token_fetcher=fetch_token))

    with pytest.raises(ProviderError) as excinfo:
        await provider.generate_from_text("a todo app")

    assert excinfo.value.detail["kind"] == StitchErrorKind.quota  # type: ignore[index]
    assert client.calls == []  # blocked before the transport was ever called
    # Near/at cap the provider is degraded (reachable but limited), not down.
    assert await provider.health() is ProviderHealth.degraded
