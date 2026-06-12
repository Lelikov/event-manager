"""Tests for the /metrics endpoint, HTTP RED middleware and webhook business counters."""

import pytest
from fastapi import FastAPI, HTTPException
from prometheus_client import REGISTRY
from starlette.requests import Request
from starlette.testclient import TestClient

from event_receiver import metrics, routes
from event_receiver.errors import IngestError, UnauthorizedError


def _sample(name: str, labels: dict[str, str]) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def _build_app() -> FastAPI:
    app = FastAPI()

    @app.get("/items/{item_id}")
    async def item(item_id: str) -> dict[str, str]:
        return {"item_id": item_id}

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/metrics")
    async def metrics_endpoint():  # noqa: ANN202
        return metrics.metrics_response()

    app.add_middleware(metrics.HttpMetricsMiddleware)
    return app


def _ingest_request(path: str = "/event/booking") -> Request:
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "server": ("testserver", 80),
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [],
    }

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"{}", "more_body": False}

    return Request(scope, receive)


class _FakeIngestController:
    def __init__(self, exc: IngestError | None = None) -> None:
        self._exc = exc

    async def ingest_booking(self, *, headers, body) -> None:  # noqa: ARG002
        if self._exc is not None:
            raise self._exc


class TestMetricsEndpoint:
    def test_metrics_route_registered(self) -> None:
        paths = {route.path for route in routes.root_router.routes}

        assert "/metrics" in paths

    def test_metrics_returns_prometheus_exposition(self) -> None:
        client = TestClient(_build_app())

        response = client.get("/metrics")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/plain")
        assert "http_requests_total" in response.text


class TestHttpRedMiddleware:
    def test_counts_by_route_template_not_raw_path(self) -> None:
        client = TestClient(_build_app())
        labels = {"method": "GET", "route": "/items/{item_id}", "status": "200"}
        before = _sample("http_requests_total", labels)
        duration_before = _sample("http_request_duration_seconds_count", {"method": "GET", "route": "/items/{item_id}"})

        client.get("/items/42")

        assert _sample("http_requests_total", labels) == before + 1
        assert (
            _sample("http_request_duration_seconds_count", {"method": "GET", "route": "/items/{item_id}"})
            == duration_before + 1
        )
        assert _sample("http_requests_total", {"method": "GET", "route": "/items/42", "status": "200"}) == 0.0

    def test_unmatched_route_recorded_as_unmatched(self) -> None:
        client = TestClient(_build_app())
        labels = {"method": "GET", "route": "unmatched", "status": "404"}
        before = _sample("http_requests_total", labels)

        client.get("/no/such/route")

        assert _sample("http_requests_total", labels) == before + 1

    def test_health_and_metrics_excluded(self) -> None:
        client = TestClient(_build_app())
        health_labels = {"method": "GET", "route": "/health", "status": "200"}
        metrics_labels = {"method": "GET", "route": "/metrics", "status": "200"}

        client.get("/health")
        client.get("/metrics")

        assert _sample("http_requests_total", health_labels) == 0.0
        assert _sample("http_requests_total", metrics_labels) == 0.0


class TestWebhookCounter:
    async def test_accepted_ingest_increments_counter(self) -> None:
        labels = {"source": "booking", "result": "accepted"}
        before = _sample("receiver_webhooks_total", labels)

        await routes._handle_ingest_request(  # noqa: SLF001
            request=_ingest_request(),
            ingest_controller=_FakeIngestController(),
            controller_method_name="ingest_booking",
        )

        assert _sample("receiver_webhooks_total", labels) == before + 1

    async def test_unauthorized_ingest_increments_counter(self) -> None:
        labels = {"source": "booking", "result": "unauthorized"}
        before = _sample("receiver_webhooks_total", labels)

        with pytest.raises(HTTPException):
            await routes._handle_ingest_request(  # noqa: SLF001
                request=_ingest_request(),
                ingest_controller=_FakeIngestController(exc=UnauthorizedError("bad key")),
                controller_method_name="ingest_booking",
            )

        assert _sample("receiver_webhooks_total", labels) == before + 1
