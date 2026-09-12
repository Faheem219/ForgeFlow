"""Prometheus exposition for the control plane (devops step 4).

Three rules shape this module:

**It is a read-only observer.** Nothing here changes request handling. The middleware times the
call, records the outcome and returns the response untouched; if recording itself raises, the
request still succeeds. An observability layer that can fail a request is worse than no metrics.

**Labels are bounded by construction.** The single thing that kills a Prometheus install is
unbounded label cardinality, and an HTTP path is the classic source: ``/projects/6712…/artifacts``
would mint a new time series per project. So the ``path`` label is always the *route template*
(``/projects/{project_id}/artifacts``), read from ``scope["route"].path_format`` after routing.
Requests that matched no route -- a scanner walking a public VM -- collapse into one
``<unmatched>`` bucket rather than one series per probed URL.

**Scrape output must never contain a secret.** Only method, route template and status code are
labelled. No query strings, no bodies, no header values, no user or project ids.

The registry is module-local rather than ``prometheus_client.REGISTRY``: a process-global default
registry makes tests order-dependent (duplicate-registration errors on re-import) and would export
whatever any dependency happened to register. Process/platform/GC collectors are attached
explicitly, so the Grafana dashboard still gets RSS, CPU and fd counts.
"""

from __future__ import annotations

import logging
import time

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from prometheus_client.gc_collector import GCCollector
from prometheus_client.platform_collector import PlatformCollector
from prometheus_client.process_collector import ProcessCollector
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)

#: Bucket for requests that matched no route. One series, however many URLs a scanner tries.
UNMATCHED = "<unmatched>"

#: Latency buckets in seconds. Tuned for this workload rather than the library default: a
#: control-plane call is either fast (sub-100ms CRUD) or slow (a model round-trip, a container
#: create, a test run), so the tail needs real resolution out to a minute.
LATENCY_BUCKETS = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    30.0,
    60.0,
    float("inf"),
)


class Metrics:
    """The control plane metric set, bound to one registry.

    Held as an object (rather than module-level globals) so a test can build a throwaway instance
    with a fresh registry and assert on exact counter values without leaking state between tests.
    """

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = registry if registry is not None else CollectorRegistry()

        self.http_requests = Counter(
            "forgeflow_http_requests_total",
            "HTTP requests handled, by method, route template and status code.",
            ("method", "path", "status"),
            registry=self.registry,
        )
        self.http_latency = Histogram(
            "forgeflow_http_request_duration_seconds",
            "Wall-clock time to produce a response, by method and route template.",
            ("method", "path"),
            buckets=LATENCY_BUCKETS,
            registry=self.registry,
        )
        self.in_flight = Gauge(
            "forgeflow_http_requests_in_flight",
            "Requests currently being handled.",
            registry=self.registry,
        )
        # Exceptions that escaped the handler entirely (the error-envelope handlers catch the
        # taxonomy ones, so this counts genuine unhandled faults) -- distinct from a 5xx response.
        self.unhandled = Counter(
            "forgeflow_http_unhandled_exceptions_total",
            "Exceptions that propagated out of a route handler.",
            ("method", "path"),
            registry=self.registry,
        )
        # A labelled constant at 1: the conventional way to expose build metadata, so Grafana can
        # show the running version and `count by (version)` reveals a half-finished rollout.
        self.build_info = Gauge(
            "forgeflow_build_info",
            "Build metadata of the running control plane (always 1).",
            ("version", "env"),
            registry=self.registry,
        )

        for collector in (ProcessCollector, PlatformCollector, GCCollector):
            try:
                collector(registry=self.registry)
            except Exception:  # pragma: no cover - platform dependent
                logger.debug("metrics: %s unavailable on this platform", collector.__name__)

    def set_build_info(self, version: str, env: str) -> None:
        self.build_info.labels(version=version, env=env).set(1)

    def render(self) -> bytes:
        return generate_latest(self.registry)


#: The process-wide instance. Created at import so route-free callers (agents, sandbox manager)
#: can record without threading a handle through every call site.
METRICS = Metrics()


def route_label(request: Request) -> str:
    """The bounded ``path`` label: a route template, or the single unmatched bucket.

    ``scope["route"]`` is populated by the Starlette router *during* ``call_next``, so this is only
    meaningful after the downstream app has run.
    """
    route = request.scope.get("route")
    path_format: object = getattr(route, "path_format", None)
    if isinstance(path_format, str) and path_format:
        return path_format
    return UNMATCHED


class MetricsMiddleware(BaseHTTPMiddleware):
    """Time every request and record its outcome.

    Deliberately records the *unhandled exception* case too, then re-raises: a route that blows up
    is exactly the thing an error-rate panel must show, and swallowing the exception here would
    turn a 500 into a hang.
    """

    def __init__(self, app: ASGIApp, metrics: Metrics | None = None) -> None:
        super().__init__(app)
        self._metrics = metrics if metrics is not None else METRICS

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        started = time.perf_counter()
        self._metrics.in_flight.inc()
        try:
            response = await call_next(request)
        except Exception:
            self._record(
                request,
                status=500,
                elapsed=time.perf_counter() - started,
                unhandled=True,
            )
            raise
        else:
            self._record(
                request,
                status=response.status_code,
                elapsed=time.perf_counter() - started,
                unhandled=False,
            )
            return response
        finally:
            self._metrics.in_flight.dec()

    def _record(self, request: Request, *, status: int, elapsed: float, unhandled: bool) -> None:
        """Record one request. Never raises -- metrics must not break the response path."""
        try:
            method = request.method
            path = route_label(request)
            self._metrics.http_requests.labels(method=method, path=path, status=str(status)).inc()
            self._metrics.http_latency.labels(method=method, path=path).observe(elapsed)
            if unhandled:
                self._metrics.unhandled.labels(method=method, path=path).inc()
        except Exception:  # pragma: no cover - defensive
            logger.warning("metrics: failed to record a request", exc_info=True)


def metrics_response(metrics: Metrics | None = None) -> Response:
    """The ``/metrics`` payload, in Prometheus text exposition format."""
    target = metrics if metrics is not None else METRICS
    return Response(content=target.render(), media_type=CONTENT_TYPE_LATEST)


__all__ = [
    "CONTENT_TYPE_LATEST",
    "LATENCY_BUCKETS",
    "METRICS",
    "UNMATCHED",
    "Metrics",
    "MetricsMiddleware",
    "metrics_response",
    "route_label",
]
