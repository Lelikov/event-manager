from typing import Any, Protocol


class ICloudEventPublisher(Protocol):
    async def publish(
        self,
        *,
        source: str,
        event_type: str,
        data: dict[str, Any],
        event_id: str | None = None,
        event_time: str | None = None,
    ) -> None: ...


class ITopologyManager(Protocol):
    async def ensure_topology(self) -> None: ...
