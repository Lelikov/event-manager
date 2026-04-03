from typing import TYPE_CHECKING, Any

import structlog
from cloudevents.http import CloudEvent, to_binary
from event_schemas.types import EVENT_PRIORITIES, EVENT_SCHEMA_VERSIONS, EventPriority, EventType
from faststream.rabbit import RabbitBroker, RabbitExchange, RabbitQueue

from event_receiver.interfaces.publisher import ICloudEventPublisher, ITopologyManager
from event_receiver.normalizers import normalize_event_payload
from event_receiver.utils import generate_idempotency_key, generate_span_id, generate_trace_id


if TYPE_CHECKING:
    from event_receiver.interfaces.routing import IEventRouter


logger = structlog.get_logger(__name__)


class CloudEventPublisher(ICloudEventPublisher):
    def __init__(
        self,
        *,
        broker: RabbitBroker,
        exchange: RabbitExchange,
        router_by_event: IEventRouter,
        getstream_decoder: callable[[str], str] | None = None,
    ) -> None:
        self._broker = broker
        self._exchange = exchange
        self._router_by_event = router_by_event
        self._getstream_decoder = getstream_decoder

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

        routing_key = self._router_by_event.resolve_routing_key_by_fields(source=source, event_type=event_type_str)
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
        event_type_enum = EventType(event_type_str) if isinstance(event_type, str) else event_type
        schema_version = EVENT_SCHEMA_VERSIONS.get(event_type_enum, "v1")
        priority = EVENT_PRIORITIES.get(event_type_enum, EventPriority.NORMAL)

        # Normalize payload to standard structure
        normalized_data = normalize_event_payload(
            event_type_enum,
            data,
            getstream_decoder=self._getstream_decoder,
        )

        # Build CloudEvent attributes with extensions
        attributes = {
            "type": event_type_str,
            "source": source,
            # CloudEvents extensions
            "traceid": trace_id,
            "spanid": span_id,
            "idempotencykey": idempotency_key,
            "dataschema": f"https://schemas.example.com/{event_type_str}/{schema_version}",
            "datacontenttype": "application/json",
            "publisherservice": "event-receiver",
            "publisherversion": "0.1.0",
        }
        if event_id:
            attributes["id"] = event_id
        if event_time:
            attributes["time"] = event_time
        if booking_id:
            attributes["booking_id"] = booking_id

        event = CloudEvent(attributes=attributes, data=normalized_data)
        headers, body = to_binary(event)

        await self._broker.publish(
            body,
            exchange=self._exchange,
            routing_key=routing_key,
            headers=headers,
            content_type=headers.pop("content-type", "application/json"),
            message_type=event_type_str,
            priority=priority.value,  # RabbitMQ priority
        )
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


class RabbitTopologyManager(ITopologyManager):
    def __init__(
        self,
        *,
        broker: RabbitBroker,
        exchange: RabbitExchange,
        topology_queues: set[str],
    ) -> None:
        self._broker = broker
        self._exchange = exchange
        self._topology_queues = topology_queues

    async def ensure_topology(self) -> None:
        logger.info(
            "Ensuring RabbitMQ topology with DLQ and priority support",
            exchange=self._exchange.name,
            queue_count=len(self._topology_queues),
        )
        declared_exchange = await self._broker.declare_exchange(self._exchange)

        # Declare Dead Letter Exchange
        dlx = RabbitExchange(name="events.dlx", type="topic", durable=True)
        declared_dlx = await self._broker.declare_exchange(dlx)
        logger.debug("Dead Letter Exchange declared", exchange=dlx.name)

        for queue_name in self._topology_queues:
            # Main queue with DLQ and priority support
            main_queue = RabbitQueue(
                name=queue_name,
                durable=True,
                routing_key=queue_name,
                arguments={
                    "x-max-priority": 10,  # Enable priority queues (0-10)
                    "x-dead-letter-exchange": "events.dlx",  # DLX for failed messages
                    "x-dead-letter-routing-key": f"{queue_name}.dlq",
                },
            )
            declared_main_queue = await self._broker.declare_queue(main_queue)
            await declared_main_queue.bind(exchange=declared_exchange, routing_key=queue_name)
            logger.debug(
                "Main queue declared with DLQ and priority",
                queue=queue_name,
                exchange=self._exchange.name,
                max_priority=10,
            )

            # Dead Letter Queue (DLQ)
            dlq = RabbitQueue(
                name=f"{queue_name}.dlq",
                durable=True,
                routing_key=f"{queue_name}.dlq",
                arguments={
                    "x-message-ttl": 86400000,  # 24 hours TTL for DLQ messages
                },
            )
            declared_dlq = await self._broker.declare_queue(dlq)
            await declared_dlq.bind(exchange=declared_dlx, routing_key=f"{queue_name}.dlq")
            logger.debug(
                "DLQ declared",
                dlq=f"{queue_name}.dlq",
                ttl_hours=24,
            )

        logger.info(
            "Rabbit topology ensured with DLQ and priority support",
            exchange=self._exchange.name,
            dlx=dlx.name,
            queues=sorted(self._topology_queues),
        )
