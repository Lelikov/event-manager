from typing import Any

import structlog
from cloudevents.http import CloudEvent, to_binary
from faststream.rabbit import RabbitBroker, RabbitExchange, RabbitQueue

from event_receiver.interfaces.publisher import ICloudEventPublisher, ITopologyManager
from event_receiver.interfaces.routing import IEventRouter


logger = structlog.get_logger(__name__)


class CloudEventPublisher(ICloudEventPublisher):
    def __init__(
        self,
        *,
        broker: RabbitBroker,
        exchange: RabbitExchange,
        router_by_event: IEventRouter,
    ) -> None:
        self._broker = broker
        self._exchange = exchange
        self._router_by_event = router_by_event

    async def publish(
        self,
        *,
        source: str,
        event_type: str,
        booking_id: str | None = None,
        data: dict[str, Any],
        event_id: str | None = None,
        event_time: str | None = None,
    ) -> None:
        routing_key = self._router_by_event.resolve_routing_key_by_fields(source=source, event_type=event_type)
        logger.debug(
            "Resolved routing key for outbound CloudEvent",
            source=source,
            event_type=event_type,
            routing_key=routing_key,
            has_event_id=event_id is not None,
            has_event_time=event_time is not None,
        )

        attributes = {
            "type": event_type,
            "source": source,
        }
        if event_id:
            attributes["id"] = event_id
        if event_time:
            attributes["time"] = event_time
        if booking_id:
            attributes["booking_id"] = booking_id

        event = CloudEvent(attributes=attributes, data=data)
        headers, body = to_binary(event)

        await self._broker.publish(
            body,
            exchange=self._exchange,
            routing_key=routing_key,
            headers=headers,
            content_type=headers.pop("content-type", "application/json"),
            message_type=event_type,
        )
        logger.info(
            "Published CloudEvent to RabbitMQ",
            source=source,
            event_type=event_type,
            routing_key=routing_key,
            exchange=self._exchange.name,
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
            "Ensuring RabbitMQ topology",
            exchange=self._exchange.name,
            queue_count=len(self._topology_queues),
        )
        declared_exchange = await self._broker.declare_exchange(self._exchange)

        for queue_name in self._topology_queues:
            queue = RabbitQueue(name=queue_name, durable=True, routing_key=queue_name)
            declared_queue = await self._broker.declare_queue(queue)
            await declared_queue.bind(exchange=declared_exchange, routing_key=queue_name)
            logger.debug(
                "Queue declared and bound",
                queue=queue_name,
                exchange=self._exchange.name,
                routing_key=queue_name,
            )

        logger.info("Rabbit topology ensured", exchange=self._exchange.name, queues=sorted(self._topology_queues))
