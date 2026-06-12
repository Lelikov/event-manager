"""Tests for the /health (liveness) and /ready (readiness) endpoints."""

from event_receiver import routes


class _FakeBroker:
    def __init__(self, *, ping_result: bool = True, ping_error: Exception | None = None) -> None:
        self._ping_result = ping_result
        self._ping_error = ping_error
        self.ping_timeout: float | None = None

    async def ping(self, timeout: float | None) -> bool:  # noqa: ASYNC109 — mirrors RabbitBroker.ping
        self.ping_timeout = timeout
        if self._ping_error is not None:
            raise self._ping_error
        return self._ping_result


class TestHealth:
    async def test_health_returns_ok(self) -> None:
        assert await routes.health() == {"status": "ok"}

    def test_routes_registered(self) -> None:
        paths = {route.path for route in routes.root_router.routes}

        assert "/health" in paths
        assert "/ready" in paths


class TestReady:
    async def test_ready_when_rabbit_reachable(self) -> None:
        broker = _FakeBroker(ping_result=True)

        response = await routes.ready(broker=broker)

        assert response.status_code == 200
        assert b'"status":"ready"' in response.body
        assert broker.ping_timeout == routes.READY_CHECK_TIMEOUT_SECONDS

    async def test_not_ready_when_ping_fails(self) -> None:
        response = await routes.ready(broker=_FakeBroker(ping_result=False))

        assert response.status_code == 503
        assert b'"status":"not_ready"' in response.body

    async def test_not_ready_when_ping_raises(self) -> None:
        response = await routes.ready(broker=_FakeBroker(ping_error=ConnectionError("down")))

        assert response.status_code == 503
        assert b'"rabbitmq":false' in response.body
