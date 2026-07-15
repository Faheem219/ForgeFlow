"""StitchAuth: transparent refresh across an expiry boundary + single-flight under concurrency."""

from __future__ import annotations

import asyncio

import pytest

from app.design.stitch_auth import StitchAuth


class _CountingFetcher:
    """Hands out tok-1, tok-2, … and counts fetches; a small await makes the race real."""

    def __init__(self, lifetime: float = 3600.0) -> None:
        self.calls = 0
        self._lifetime = lifetime

    async def __call__(self) -> tuple[str, float]:
        self.calls += 1
        await asyncio.sleep(0.01)  # yield so concurrent callers pile onto the lock
        return f"tok-{self.calls}", self._lifetime


async def test_token_is_cached_until_near_expiry() -> None:
    fetcher = _CountingFetcher()
    now = [1000.0]
    auth = StitchAuth(token_fetcher=fetcher, clock=lambda: now[0])

    assert await auth.get_access_token() == "tok-1"
    assert await auth.get_access_token() == "tok-1"  # served from cache
    assert fetcher.calls == 1


async def test_token_refreshes_transparently_after_expiry() -> None:
    fetcher = _CountingFetcher(lifetime=3600.0)
    now = [1000.0]
    auth = StitchAuth(token_fetcher=fetcher, clock=lambda: now[0])

    assert await auth.get_access_token() == "tok-1"

    # Advance the clock past (lifetime - skew): still cached just before, refreshed just after.
    now[0] = 1000.0 + 3600.0 - 60.0 - 1.0  # skew default is 60s
    assert await auth.get_access_token() == "tok-1"

    now[0] = 1000.0 + 3600.0  # fully expired
    assert await auth.get_access_token() == "tok-2"
    assert fetcher.calls == 2


async def test_concurrent_callers_trigger_a_single_refresh() -> None:
    fetcher = _CountingFetcher()
    now = [1000.0]
    auth = StitchAuth(token_fetcher=fetcher, clock=lambda: now[0])

    tokens = await asyncio.gather(*(auth.get_access_token() for _ in range(10)))

    assert set(tokens) == {"tok-1"}  # everyone got the same token
    assert fetcher.calls == 1  # single-flight: only one fetch happened


async def test_invalidate_forces_a_refresh() -> None:
    fetcher = _CountingFetcher()
    now = [1000.0]
    auth = StitchAuth(token_fetcher=fetcher, clock=lambda: now[0])

    assert await auth.get_access_token() == "tok-1"
    auth.invalidate()
    assert await auth.get_access_token() == "tok-2"
    assert fetcher.calls == 2


async def test_default_fetcher_errors_when_credentials_absent() -> None:
    from app.core.errors import ProviderError

    auth = StitchAuth()  # real fetcher, but no STITCH_* creds configured in tests
    with pytest.raises(ProviderError):
        await auth.get_access_token()
