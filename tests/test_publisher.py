"""Tests for CloudEventPublisher and RabbitTopologyManager."""

import json
from typing import Any

import pytest
import structlog.testing
from aio_pika.exceptions import DeliveryError
from faststream.rabbit import ExchangeType, RabbitExchange

from event_receiver.adapters.publisher import CloudEventPublisher, RabbitTopologyManager
from event_receiver.errors import ConfigurationError, PublishUnavailableError
from event_receiver.routing import EventRouter
from tests.test_ingest_controller import make_settings


class FakeBroker:
    def __init__(self, error: Exception | None = None) -> None:
        self.published: list[dict[str, Any]] = []
        self._error = error

    async def publish(self, body: bytes, **kwargs: Any) -> None:
        if self._error is not None:
            raise self._error
        self.published.append({"body": body, **kwargs})


class FakeUserResolver:
    def __init__(self, user_ids: dict[str, str] | None = None) -> None:
        self.user_ids = user_ids if user_ids is not None else {}
        self.calls: list[tuple[str, str | None]] = []

    async def resolve_or_create(self, *, email: str, role: str | None = None) -> str | None:
        self.calls.append((email, role))
        return self.user_ids.get(email)


def make_publisher(
    broker: FakeBroker,
    *,
    user_resolver: FakeUserResolver | None = None,
    publish_timeout: float = 10.0,
) -> CloudEventPublisher:
    settings = make_settings()
    return CloudEventPublisher(
        broker=broker,  # type: ignore[arg-type]
        exchange=RabbitExchange(name="events", type=ExchangeType.TOPIC, durable=True),
        router_by_event=EventRouter(settings.routing),
        user_resolver=user_resolver if user_resolver is not None else FakeUserResolver(),
        publish_timeout=publish_timeout,
    )


class TestPublish:
    async def test_known_type_resolves_routing_key_and_priority(self) -> None:
        broker = FakeBroker()
        publisher = make_publisher(broker)
        await publisher.publish(
            source="booking",
            event_type="booking.cancelled",
            booking_id="uid-1",
            data={"users": [], "cancellation_reason": "x"},
        )
        assert len(broker.published) == 1
        published = broker.published[0]
        assert published["routing_key"] == "events.booking.lifecycle"
        assert published["priority"] == 10
        assert published["timeout"] == 10.0

    async def test_unknown_event_type_routes_to_unrouted_without_error(self) -> None:
        broker = FakeBroker()
        publisher = make_publisher(broker)
        await publisher.publish(
            source="getstream",
            event_type="getstream.health.check",
            data={"type": "health.check"},
        )
        assert len(broker.published) == 1
        assert broker.published[0]["routing_key"] == "events.unrouted"

    async def test_publish_timeout_maps_to_publish_unavailable(self) -> None:
        broker = FakeBroker(error=TimeoutError())
        publisher = make_publisher(broker, publish_timeout=0.01)
        with pytest.raises(PublishUnavailableError, match="did not confirm"):
            await publisher.publish(source="booking", event_type="booking.cancelled", data={"users": []})

    async def test_returned_message_maps_to_publish_unavailable(self) -> None:
        broker = FakeBroker(error=DeliveryError(None, None))  # type: ignore[arg-type]
        publisher = make_publisher(broker)
        with pytest.raises(PublishUnavailableError, match="could not route"):
            await publisher.publish(source="booking", event_type="booking.cancelled", data={"users": []})

    async def test_participants_enriched_with_user_id(self) -> None:
        broker = FakeBroker()
        resolver = FakeUserResolver(user_ids={"client@example.com": "uuid-1"})
        publisher = make_publisher(broker, user_resolver=resolver)
        await publisher.publish(
            source="booking",
            event_type="booking.cancelled",
            booking_id="uid-1",
            data={"users": [{"email": "client@example.com", "role": "client"}]},
        )
        data = json.loads(broker.published[0]["body"])
        assert data["normalized"]["participants"][0]["user_id"] == "uuid-1"

    async def test_unresolved_user_id_logs_structured_warning(self) -> None:
        broker = FakeBroker()
        publisher = make_publisher(broker, user_resolver=FakeUserResolver(user_ids={}))
        with structlog.testing.capture_logs() as logs:
            await publisher.publish(
                source="booking",
                event_type="booking.cancelled",
                booking_id="uid-1",
                data={"users": [{"email": "client@example.com", "role": "client"}]},
            )
        assert len(broker.published) == 1
        warnings = [entry for entry in logs if entry.get("log_level") == "warning"]
        assert any("user_id" in entry["event"] for entry in warnings)


class TestDuplicateSuppression:
    async def test_same_idempotency_key_published_once(self) -> None:
        broker = FakeBroker()
        publisher = make_publisher(broker)
        for _ in range(3):
            await publisher.publish(
                source="booking",
                event_type="booking.cancelled",
                booking_id="uid-1",
                data={"users": [], "cancellation_reason": "x"},
            )
        assert len(broker.published) == 1

    async def test_different_payloads_are_not_suppressed(self) -> None:
        broker = FakeBroker()
        publisher = make_publisher(broker)
        await publisher.publish(source="booking", event_type="booking.cancelled", booking_id="u1", data={"a": 1})
        await publisher.publish(source="booking", event_type="booking.cancelled", booking_id="u1", data={"a": 2})
        assert len(broker.published) == 2

    async def test_expired_cache_entry_allows_republish(self) -> None:
        broker = FakeBroker()
        publisher = make_publisher(broker)
        publisher._idempotency_cache_ttl = 0.0  # noqa: SLF001
        await publisher.publish(source="booking", event_type="booking.cancelled", booking_id="u1", data={"a": 1})
        await publisher.publish(source="booking", event_type="booking.cancelled", booking_id="u1", data={"a": 1})
        assert len(broker.published) == 2

    async def test_failed_publish_is_not_remembered(self) -> None:
        broker = FakeBroker(error=TimeoutError())
        publisher = make_publisher(broker)
        with pytest.raises(PublishUnavailableError):
            await publisher.publish(source="booking", event_type="booking.cancelled", booking_id="u1", data={"a": 1})
        broker._error = None  # noqa: SLF001
        await publisher.publish(source="booking", event_type="booking.cancelled", booking_id="u1", data={"a": 1})
        assert len(broker.published) == 1


class TestTopologyValidation:
    def make_manager(self, required: frozenset[str]) -> RabbitTopologyManager:
        return RabbitTopologyManager(
            broker=FakeBroker(),  # type: ignore[arg-type]
            exchange=RabbitExchange(name="events", type=ExchangeType.TOPIC, durable=True),
            required_destinations=required,
        )

    async def test_destination_without_queue_binding_fails_startup(self) -> None:
        manager = self.make_manager(frozenset({"events.nonexistent.destination"}))
        with pytest.raises(ConfigurationError, match="nonexistent"):
            await manager.ensure_topology()

    async def test_canonical_destinations_pass_validation(self) -> None:
        settings = make_settings()
        manager = self.make_manager(frozenset(settings.routing_destinations))
        manager._validate_destinations()  # noqa: SLF001
