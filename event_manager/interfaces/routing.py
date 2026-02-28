from typing import Protocol


class IEventRouter(Protocol):
    def resolve_routing_key_by_fields(self, *, source: str, event_type: str) -> str: ...
