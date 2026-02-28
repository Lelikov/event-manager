import logging
from typing import Any

from cloudevents.http import CloudEvent, to_binary
from faststream.rabbit import RabbitBroker, RabbitExchange, RabbitQueue

from event_manager.interfaces.publisher import ICloudEventPublisher, ITopologyManager
from event_manager.interfaces.routing import IEventRouter


logger = logging.getLogger(__name__)


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
        data: dict[str, Any],
        event_id: str | None = None,
        event_time: str | None = None,
    ) -> None:
        routing_key = self._router_by_event.resolve_routing_key_by_fields(source=source, event_type=event_type)

        attributes = {
            "type": event_type,
            "source": source,
        }
        if event_id:
            attributes["id"] = event_id
        if event_time:
            attributes["time"] = event_time

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
        declared_exchange = await self._broker.declare_exchange(self._exchange)

        for queue_name in self._topology_queues:
            queue = RabbitQueue(name=queue_name, durable=True, routing_key=queue_name)
            declared_queue = await self._broker.declare_queue(queue)
            await declared_queue.bind(exchange=declared_exchange, routing_key=queue_name)

        logger.info(
            "Rabbit topology ensured: exchange=%s queues=%s",
            self._exchange.name,
            sorted(self._topology_queues),
        )
