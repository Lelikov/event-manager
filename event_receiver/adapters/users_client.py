from http import HTTPStatus

import structlog
from httpx import AsyncClient

from event_receiver.interfaces.users import IUserResolver


logger = structlog.get_logger(__name__)


class UserResolver(IUserResolver):
    def __init__(self, *, http_client: AsyncClient, api_token: str) -> None:
        self._client = http_client
        self._headers = {"Authorization": f"Bearer {api_token}"}

    async def resolve_or_create(self, *, email: str, role: str) -> str:
        user_id = await self._get_user(email=email, role=role)
        if user_id:
            return user_id
        return await self._create_user(email=email, role=role)

    async def _get_user(self, *, email: str, role: str) -> str | None:
        response = await self._client.get(
            f"/api/users/roles/{role}/emails/{email}",
            headers=self._headers,
        )
        if response.status_code == HTTPStatus.NOT_FOUND:
            return None
        response.raise_for_status()
        return response.json()["id"]

    async def _create_user(self, *, email: str, role: str) -> str:
        response = await self._client.post(
            "/api/users",
            json={"email": email, "role": role},
            headers=self._headers,
        )
        if response.status_code == HTTPStatus.CONFLICT:
            # Race condition: another process created the user between our GET and POST
            logger.debug("User conflict on create, retrying GET", email=email, role=role)
            user_id = await self._get_user(email=email, role=role)
            if user_id is None:
                msg = f"User {email!r} role={role!r} not found after 409 conflict"
                raise RuntimeError(msg)
            return user_id
        response.raise_for_status()
        logger.info("Created user in event-users", email=email, role=role, user_id=response.json()["id"])
        return response.json()["id"]
