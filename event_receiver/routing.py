import fnmatch
from dataclasses import dataclass

import structlog
from cloudevents.pydantic import CloudEvent
from pydantic import BaseModel, Field


logger = structlog.get_logger(__name__)


class RouteRule(BaseModel):
    destination: str = Field(..., description="Rabbit routing key")
    source_pattern: str = Field(default="*", description="CloudEvent source glob")
    type_pattern: str = Field(default="*", description="CloudEvent type glob")

    def matches(self, source: str, event_type: str) -> bool:
        return fnmatch.fnmatch(source, self.source_pattern) and fnmatch.fnmatch(event_type, self.type_pattern)


@dataclass(frozen=True)
class RoutingConfig:
    default_destination: str
    rules: list[RouteRule]


class EventRouter:
    def __init__(self, config: RoutingConfig) -> None:
        self._default_destination = config.default_destination
        self._rules = config.rules
        logger.debug(
            "EventRouter initialized",
            default_destination=self._default_destination,
            rules_count=len(self._rules),
        )

    def resolve_routing_key(self, event: CloudEvent) -> str:
        return self.resolve_routing_key_by_fields(
            source=str(event.source),
            event_type=event.type,
        )

    def resolve_routing_key_by_fields(self, source: str, event_type: str) -> str:
        source = str(source)
        event_type = str(event_type)

        for rule in self._rules:
            if rule.matches(source=source, event_type=event_type):
                logger.debug(
                    "Routing rule matched",
                    source=source,
                    event_type=event_type,
                    destination=rule.destination,
                    source_pattern=rule.source_pattern,
                    type_pattern=rule.type_pattern,
                )
                return rule.destination

        logger.debug(
            "No routing rule matched, using default destination",
            source=source,
            event_type=event_type,
            destination=self._default_destination,
        )
        return self._default_destination
