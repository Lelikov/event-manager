from collections.abc import Mapping
from typing import Protocol

from event_manager.schemas import FreeFormIngestRequest


class IIngestController(Protocol):
    async def ingest_cloudevent(self, *, headers: Mapping[str, str], body: bytes) -> None: ...

    async def ingest_frontend(self, *, payload: FreeFormIngestRequest, token: str | None) -> None: ...

    async def ingest_backend(self, *, headers: Mapping[str, str], body: bytes) -> None: ...
