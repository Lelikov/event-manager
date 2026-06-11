import json
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import anyio
import structlog
from aio_pika.exceptions import DeliveryError
from cloudevents.core.bindings.http import to_binary
from cloudevents.core.formats.json import JSONFormat
from cloudevents.core.v1.event import CloudEvent
from event_schemas.attributes import (
    BOOKING_ID_ATTRIBUTE,
    IDEMPOTENCY_KEY_ATTRIBUTE,
    SPAN_ID_ATTRIBUTE,
    TRACE_ID_ATTRIBUTE,
)
from event_schemas.queues import ALL_QUEUES, DEFAULT_ROUTING_KEY, EVENTS_DLX, QueueSpec
from event_schemas.types import EVENT_PRIORITIES, EVENT_SCHEMA_VERSIONS, EventPriority, EventType
from faststream.rabbit import ExchangeType, RabbitBroker, RabbitExchange, RabbitQueue

from event_receiver.errors import ConfigurationError, PublishUnavailableError
from event_receiver.interfaces.publisher import ICloudEventPublisher, ITopologyManager
from event_receiver.interfaces.users import IUserResolver
from event_receiver.normalizers import normalize_event_payload
from event_receiver.utils import generate_idempotency_key, generate_span_id, generate_trace_id


if TYPE_CHECKING:
    from collections.abc import Callable

    from event_receiver.interfaces.routing import IEventRouter


logger = structlog.get_logger(__name__)

_PUBLISHED_EVENTS_LOG_FILE = Path("published_events.jsonl")


def _coerce_event_type(event_type: str) -> EventType | None:
    """Return the EventType member for a wire type string, or None if unknown."""
    try:
        return EventType(event_type)
    except ValueError:
        return None


class CloudEventPublisher(ICloudEventPublisher):
    def __init__(
        self,
        *,
        broker: RabbitBroker,
        exchange: RabbitExchange,
        router_by_event: IEventRouter,
        user_resolver: IUserResolver,
        getstream_decoder: Callable[[str], str] | None = None,
        publish_timeout: float = 10.0,
        debug: bool = False,
    ) -> None:
        self._broker = broker
        self._exchange = exchange
        self._router_by_event = router_by_event
        self._user_resolver = user_resolver
        self._getstream_decoder = getstream_decoder
        self._publish_timeout = publish_timeout
        self._debug = debug

    async def publish(
        self,
        *,
        source: str,
        event_type: str | EventType,
        booking_id: str | None = None,
        data: dict[str, Any],
        event_id: str | None = None,
        event_time: str | None = None,
        trace_id: str | None = None,
    ) -> None:
        # Convert EventType enum to string if needed
        event_type_str = event_type.value if isinstance(event_type, EventType) else event_type
        event_type_enum = _coerce_event_type(event_type_str)

        routing_key = self._router_by_event.resolve_routing_key_by_fields(source=source, event_type=event_type_str)
        if event_type_enum is None:
            # Unknown type (e.g. a new GetStream webhook type): never 500 —
            # park it in the unrouted queue with full payload for later triage.
            routing_key = str(DEFAULT_ROUTING_KEY)
            logger.warning(
                "Unknown event type, routing to unrouted queue",
                source=source,
                event_type=event_type_str,
                routing_key=routing_key,
            )
        logger.debug(
            "Resolved routing key for outbound CloudEvent",
            source=source,
            event_type=event_type_str,
            routing_key=routing_key,
            has_event_id=event_id is not None,
            has_event_time=event_time is not None,
            has_trace_id=trace_id is not None,
        )

        # Generate tracing IDs
        trace_id = trace_id or generate_trace_id()
        span_id = generate_span_id()

        # Generate idempotency key
        idempotency_key = generate_idempotency_key(
            event_type=event_type_str,
            booking_id=booking_id,
            data=data,
        )

        # Get schema version and priority
        schema_version = EVENT_SCHEMA_VERSIONS.get(event_type_enum, "v1")
        priority = EVENT_PRIORITIES.get(event_type_enum, EventPriority.NORMAL)

        # Normalize payload to standard structure
        normalized_data = normalize_event_payload(
            event_type_enum,
            data,
            getstream_decoder=self._getstream_decoder,
        )

        # Enrich each participant with their user_id from event-users (skip if already resolved)
        for participant in normalized_data["normalized"]["participants"]:
            if not participant.get("user_id"):
                user_id = await self._user_resolver.resolve_or_create(
                    email=participant["email"],
                    role=participant.get("role"),
                )
                if user_id:
                    participant["user_id"] = user_id

        # Build CloudEvent attributes with extensions
        attributes = {
            "type": event_type_str,
            "source": source,
            # CloudEvents extensions (canonical names from event_schemas.attributes)
            TRACE_ID_ATTRIBUTE: trace_id,
            SPAN_ID_ATTRIBUTE: span_id,
            IDEMPOTENCY_KEY_ATTRIBUTE: idempotency_key,
            "dataschema": f"https://schemas.example.com/{event_type_str}/{schema_version}",
            "datacontenttype": "application/json",
            "publisherservice": "event-receiver",
            "publisherversion": "0.1.0",
        }
        if event_id:
            attributes["id"] = event_id
        if event_time:
            # CloudEvents 2.0 requires datetime, not string
            if isinstance(event_time, str):
                event_time = datetime.fromisoformat(event_time)
            attributes["time"] = event_time
        if booking_id:
            attributes[BOOKING_ID_ATTRIBUTE] = booking_id

        event = CloudEvent(attributes=attributes, data=json.dumps(normalized_data).encode())
        message = to_binary(event, JSONFormat())
        headers = dict(message.headers)

        await self._publish_to_broker(
            body=message.body,
            routing_key=routing_key,
            headers=headers,
            event_type_str=event_type_str,
            priority=priority,
        )
        if self._debug:
            record = {
                "ts": time.time(),
                "source": source,
                "event_type": event_type_str,
                "routing_key": routing_key,
                "exchange": self._exchange.name,
                "trace_id": trace_id,
                "idempotency_key": idempotency_key,
                "booking_id": booking_id,
                "priority": priority.value,
                "data": normalized_data,
            }
            async with await anyio.open_file(_PUBLISHED_EVENTS_LOG_FILE, "a") as f:
                await f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

        logger.info(
            "Published CloudEvent to RabbitMQ",
            source=source,
            event_type=event_type_str,
            routing_key=routing_key,
            exchange=self._exchange.name,
            trace_id=trace_id,
            idempotency_key=idempotency_key,
            priority=priority.value,
        )

    async def _publish_to_broker(
        self,
        *,
        body: bytes,
        routing_key: str,
        headers: dict[str, Any],
        event_type_str: str,
        priority: EventPriority,
    ) -> None:
        """Publish to the broker mapping confirm timeouts and returned messages to PublishUnavailableError."""
        try:
            await self._broker.publish(
                body,
                exchange=self._exchange,
                routing_key=routing_key,
                headers=headers,
                content_type="application/json",
                message_type=event_type_str,
                priority=priority.value,  # RabbitMQ priority
                timeout=self._publish_timeout,
            )
        except TimeoutError as exc:
            logger.exception(
                "Publish confirm timed out (broker blocked or unavailable)",
                event_type=event_type_str,
                routing_key=routing_key,
                timeout=self._publish_timeout,
            )
            raise PublishUnavailableError("Broker did not confirm publish in time") from exc
        except DeliveryError as exc:
            logger.exception(
                "Message returned by broker (unroutable or rejected)",
                event_type=event_type_str,
                routing_key=routing_key,
                error=str(exc),
            )
            raise PublishUnavailableError(f"Broker could not route message to {routing_key!r}") from exc


class RabbitTopologyManager(ITopologyManager):
    """Declares the FULL canonical broker topology from event_schemas.queues.ALL_QUEUES."""

    def __init__(
        self,
        *,
        broker: RabbitBroker,
        exchange: RabbitExchange,
        queue_specs: tuple[QueueSpec, ...] = ALL_QUEUES,
        required_destinations: frozenset[str] = frozenset(),
    ) -> None:
        self._broker = broker
        self._exchange = exchange
        self._queue_specs = queue_specs
        self._required_destinations = required_destinations

    def _validate_destinations(self) -> None:
        """Fail startup if a routing destination has no queue binding — otherwise publishes silently vanish."""
        bindings = {str(spec.binding) for spec in self._queue_specs}
        missing = self._required_destinations - bindings
        if missing:
            raise ConfigurationError(
                f"Routing destinations without a bound queue: {sorted(missing)}; declared bindings: {sorted(bindings)}",
            )

    async def ensure_topology(self) -> None:
        self._validate_destinations()
        logger.info(
            "Ensuring canonical RabbitMQ topology",
            exchange=self._exchange.name,
            queue_count=len(self._queue_specs),
        )
        declared_exchange = await self._broker.declare_exchange(self._exchange)

        dlx = RabbitExchange(name=EVENTS_DLX, type=ExchangeType.TOPIC, durable=True)
        declared_dlx = await self._broker.declare_exchange(dlx)
        logger.debug("Dead Letter Exchange declared", exchange=dlx.name)

        for spec in self._queue_specs:
            main_queue = RabbitQueue(
                name=spec.name,
                durable=True,
                routing_key=str(spec.binding),
                arguments=spec.arguments,
            )
            declared_main_queue = await self._broker.declare_queue(main_queue)
            await declared_main_queue.bind(exchange=declared_exchange, routing_key=str(spec.binding))
            logger.debug(
                "Queue declared",
                queue=spec.name,
                binding=str(spec.binding),
                consumer=spec.consumer,
            )

            dlq = RabbitQueue(
                name=spec.dlq_name,
                durable=True,
                routing_key=spec.dlq_name,
                arguments=spec.dlq_arguments,
            )
            declared_dlq = await self._broker.declare_queue(dlq)
            await declared_dlq.bind(exchange=declared_dlx, routing_key=spec.dlq_name)
            logger.debug("DLQ declared", dlq=spec.dlq_name)

        logger.info(
            "Rabbit topology ensured",
            exchange=self._exchange.name,
            dlx=dlx.name,
            queues=sorted(spec.name for spec in self._queue_specs),
        )
