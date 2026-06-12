"""Prometheus metrics for event-receiver.

Module-level metric objects (idiomatic for prometheus-client) plus the HTTP RED
middleware. HTTP labels always use the route template — never the raw path — to
keep cardinality bounded.
"""

import time
from typing import TYPE_CHECKING

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response


if TYPE_CHECKING:
    from starlette.middleware.base import RequestResponseEndpoint
    from starlette.requests import Request


UNMATCHED_ROUTE = "unmatched"
RED_EXCLUDED_ROUTES = frozenset({"/metrics", "/health"})

HTTP_REQUESTS_TOTAL = Counter(
    "http_requests_total",
    "HTTP requests by method, route template and status code.",
    ["method", "route", "status"],
)
HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds by method and route template.",
    ["method", "route"],
)

WEBHOOKS_TOTAL = Counter(
    "receiver_webhooks_total",
    "Ingested webhooks by source and result.",
    ["source", "result"],
)
PUBLISH_FAILURES_TOTAL = Counter(
    "receiver_publish_failures_total",
    "CloudEvent publish failures by reason.",
    ["reason"],
)
UNKNOWN_EVENT_TYPES_TOTAL = Counter(
    "receiver_unknown_event_types_total",
    "Events with a type unknown to the EventType enum (parked in events.unrouted).",
    ["source"],
)


def route_template(request: Request) -> str:
    route = request.scope.get("route")
    if route is None:
        return UNMATCHED_ROUTE
    return route.path


class HttpMetricsMiddleware(BaseHTTPMiddleware):
    """RED middleware: counts requests and observes latency, labeled by route template."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        started_at = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            self._record(request, status_code=500, started_at=started_at)
            raise
        self._record(request, status_code=response.status_code, started_at=started_at)
        return response

    @staticmethod
    def _record(request: Request, *, status_code: int, started_at: float) -> None:
        route = route_template(request)
        if route in RED_EXCLUDED_ROUTES:
            return
        HTTP_REQUESTS_TOTAL.labels(method=request.method, route=route, status=str(status_code)).inc()
        HTTP_REQUEST_DURATION_SECONDS.labels(method=request.method, route=route).observe(
            time.perf_counter() - started_at,
        )


def metrics_response() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
