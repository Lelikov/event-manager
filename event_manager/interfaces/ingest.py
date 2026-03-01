from collections.abc import Mapping
from typing import Protocol


class IIngestController(Protocol):
    async def ingest_cloudevent(self, *, headers: Mapping[str, str], body: bytes) -> None: ...

    async def ingest_unisender_go(self, *, headers: Mapping[str, str], body: bytes) -> None: ...
