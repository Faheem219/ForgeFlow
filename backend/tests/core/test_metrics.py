"""The Prometheus layer (devops step 4).

The properties worth testing are the ones that bite in production: label cardinality stays bounded
however hostile the traffic, the middleware never changes what a caller receives, and a failure in
the metrics code itself cannot take a request down with it.
"""

from __future__ import annotations

from fastapi import FastAPI
from prometheus_client import CollectorRegistry
from starlette.responses import Response
from starlette.testclient import TestClient

from app.core.metrics import (
    CONTENT_TYPE_LATEST,
    UNMATCHED,
    Metrics,
    MetricsMiddleware,
    metrics_response,
)


def _app(metrics: Metrics) -> FastAPI:
    app = FastAPI()
    app.add_middleware(MetricsMiddleware, metrics=metrics)

    @app.get("/projects/{project_id}/artifacts")
    async def artifacts(project_id: str) -> dict[str, str]:
        return {"project_id": project_id}

    @app.get("/boom")
    async def boom() -> dict[str, str]:
        raise RuntimeError("kaboom")

    @app.get("/metrics", include_in_schema=False)
    async def metrics_endpoint() -> Response:
        return metrics_response(metrics)

    return app


def _sample(metrics: Metrics, name: str, **labels: str) -> float:
    value = metrics.registry.get_sample_value(name, labels or None)
    return 0.0 if value is None else value


def test_the_path_label_is_the_route_template_not_the_concrete_url() -> None:
    """One series per route, not one per project — the cardinality guarantee."""
    metrics = Metrics(CollectorRegistry())
    client = TestClient(_app(metrics))

    for project_id in ("aaa111", "bbb222", "ccc333"):
        assert client.get(f"/projects/{project_id}/artifacts").status_code == 200

    template = "/projects/{project_id}/artifacts"
    assert (
        _sample(
            metrics,
            "forgeflow_http_requests_total",
            method="GET",
            path=template,
            status="200",
        )
        == 3.0
    )
    # No series leaked under a concrete id.
    for project_id in ("aaa111", "bbb222", "ccc333"):
        assert (
            metrics.registry.get_sample_value(
                "forgeflow_http_requests_total",
                {"method": "GET", "path": f"/projects/{project_id}/artifacts", "status": "200"},
            )
            is None
        )


def test_unmatched_paths_collapse_into_one_bucket() -> None:
    """A scanner probing a public VM must not be able to mint a time series per URL it tries."""
    metrics = Metrics(CollectorRegistry())
    client = TestClient(_app(metrics))

    for probe in ("/.env", "/wp-admin", "/admin.php", "/../../etc/passwd"):
        assert client.get(probe).status_code == 404

    assert (
        _sample(
            metrics,
            "forgeflow_http_requests_total",
            method="GET",
            path=UNMATCHED,
            status="404",
        )
        == 4.0
    )


def test_an_unhandled_exception_is_counted_and_still_propagates() -> None:
    """A 500 must show up on the error-rate panel *and* still be a 500 to the caller."""
    metrics = Metrics(CollectorRegistry())
    client = TestClient(_app(metrics), raise_server_exceptions=False)

    assert client.get("/boom").status_code == 500

    assert (
        _sample(metrics, "forgeflow_http_unhandled_exceptions_total", method="GET", path="/boom")
        == 1.0
    )
    assert (
        _sample(metrics, "forgeflow_http_requests_total", method="GET", path="/boom", status="500")
        == 1.0
    )


def test_in_flight_returns_to_zero_even_when_the_handler_raises() -> None:
    """A leaked in-flight gauge would climb forever and make the panel useless."""
    metrics = Metrics(CollectorRegistry())
    client = TestClient(_app(metrics), raise_server_exceptions=False)

    client.get("/projects/x/artifacts")
    client.get("/boom")

    assert _sample(metrics, "forgeflow_http_requests_in_flight") == 0.0


def test_latency_is_observed_into_the_histogram() -> None:
    metrics = Metrics(CollectorRegistry())
    client = TestClient(_app(metrics))
    client.get("/projects/x/artifacts")

    template = "/projects/{project_id}/artifacts"
    assert (
        _sample(
            metrics, "forgeflow_http_request_duration_seconds_count", method="GET", path=template
        )
        == 1.0
    )


def test_the_endpoint_renders_prometheus_text_format() -> None:
    metrics = Metrics(CollectorRegistry())
    client = TestClient(_app(metrics))
    client.get("/projects/x/artifacts")

    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(CONTENT_TYPE_LATEST.split(";")[0])
    body = response.text
    assert "# TYPE forgeflow_http_requests_total counter" in body
    assert "forgeflow_http_request_duration_seconds_bucket" in body


def test_build_info_is_a_labelled_constant() -> None:
    """`count by (version)` over this is how a half-finished rollout becomes visible."""
    metrics = Metrics(CollectorRegistry())
    metrics.set_build_info(version="1.2.3", env="prod")

    assert _sample(metrics, "forgeflow_build_info", version="1.2.3", env="prod") == 1.0


def test_recording_failures_never_break_the_response() -> None:
    """If the metrics code itself throws, the caller must still get their response."""
    metrics = Metrics(CollectorRegistry())

    class Exploding:
        def labels(self, **_: str) -> object:
            raise RuntimeError("registry is on fire")

    metrics.http_requests = Exploding()  # type: ignore[assignment]
    client = TestClient(_app(metrics))

    response = client.get("/projects/x/artifacts")
    assert response.status_code == 200
    assert response.json() == {"project_id": "x"}


def test_scrape_output_carries_no_query_string_or_body() -> None:
    """Only method, route template and status are labelled — nothing user-supplied."""
    metrics = Metrics(CollectorRegistry())
    client = TestClient(_app(metrics))
    client.get("/projects/x/artifacts?token=super-secret-value&email=a@b.com")

    body = client.get("/metrics").text
    assert "super-secret-value" not in body
    assert "a@b.com" not in body
