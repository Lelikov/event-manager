from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class IngressKind(StrEnum):
    FRONTEND = "frontend"
    BACKEND = "backend"
    INTERNAL = "internal"


class FreeFormIngestRequest(BaseModel):
    source: str | None = None
    type: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
