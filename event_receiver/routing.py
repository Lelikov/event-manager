import fnmatch
from dataclasses import dataclass

from cloudevents.pydantic import CloudEvent
from pydantic import BaseModel, Field


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
                return rule.destination

        return self._default_destination
