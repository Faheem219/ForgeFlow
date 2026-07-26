"""The FE dev server must accept the hostnames the preview proxy actually uses.

Vite >= 5.4.12 answers **403** to a `Host` header it does not recognise, and Caddy forwards the
original host. `*.localhost` is allowed by Vite itself (which is why local preview survives), but a
real `PREVIEW_BASE_DOMAIN` is not — so the platform passes the allowlist to the skeleton's
vite.config via `VITE_ALLOWED_HOSTS`. Verified against a real dev server: an allowlisted host gets
200, an unlisted one still gets 403.
"""

from __future__ import annotations

import pytest

from app.core.config import reset_config
from app.sandbox.manager import container_name
from app.sandbox.preview import allowed_preview_hosts, preview_urls


def test_local_preview_hosts_are_allowed() -> None:
    hosts = allowed_preview_hosts("p1")

    assert "p1.preview.localhost" in hosts  # the FE URL the iframe loads
    assert "p1.api.preview.localhost" in hosts  # the BE URL the FE calls
    # How the proxy addresses the sandbox, and how in-sandbox probes/Playwright do.
    assert container_name("p1") in hosts
    assert "localhost" in hosts and "127.0.0.1" in hosts


def test_a_production_preview_domain_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The case that would 403 without this: a non-`.localhost` proxied hostname."""
    monkeypatch.setenv("PREVIEW_BASE_DOMAIN", "preview.forgeflow.app")
    reset_config()

    hosts = allowed_preview_hosts("p1")
    fe_url, be_url = preview_urls("p1")

    assert "p1.preview.forgeflow.app" in hosts
    assert "p1.api.preview.forgeflow.app" in hosts
    for url in (fe_url, be_url):
        assert any(url.endswith(host) for host in hosts)


def test_the_allowlist_has_no_blanks_or_duplicates() -> None:
    """It is joined with commas into one env var — empties would widen it to everything."""
    hosts = allowed_preview_hosts("p1")

    assert all(host and host.strip() == host for host in hosts)
    assert len(hosts) == len(set(hosts))
    assert "*" not in hosts
