from typing import Protocol


class IUserResolver(Protocol):
    async def resolve_or_create(self, *, email: str, role: str) -> str:
        """Return user UUID (str) from event-users, creating the user if not found."""
        ...
