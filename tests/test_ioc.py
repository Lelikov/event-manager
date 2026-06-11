"""Tests for DI providers."""

import structlog.testing

from event_receiver.ioc import AppProvider
from tests.test_ingest_controller import make_settings


class TestProvideFaststreamRouter:
    def test_broker_password_is_not_logged(self) -> None:
        settings = make_settings(rabbit_url="amqp://rabbit-user:s3cr3t-password@rabbit.local:5672/vhost1")
        with structlog.testing.capture_logs() as logs:
            AppProvider().provide_faststream_router(settings)
        assert logs
        for entry in logs:
            assert "s3cr3t-password" not in str(entry)
