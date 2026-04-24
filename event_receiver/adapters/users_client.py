from http import HTTPStatus

import httpx
import structlog
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from event_receiver.interfaces.users import IUserResolver


logger = structlog.get_logger(__name__)

_RETRY_DECORATOR = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.HTTPStatusError)),
    before_sleep=lambda retry_state: logger.warning(
        "Retrying event-users call",
        attempt=retry_state.attempt_number,
        error=repr(retry_state.outcome.exception()),
    ),
    reraise=True,
)


class UserResolver(IUserResolver):
    def __init__(self, *, http_client: httpx.AsyncClient, api_token: str) -> None:
        self._client = http_client
        self._headers = {"Authorization": f"Bearer {api_token}"}

    async def resolve_or_create(self, *, email: str, role: str) -> str | None:
        try:
            return await self._resolve_or_create_with_retry(email=email, role=role)
        except httpx.HTTPError, RuntimeError:
            logger.warning("event-users unavailable, publishing without user_id", email=email, role=role)
            return None

    @_RETRY_DECORATOR
    async def _resolve_or_create_with_retry(self, *, email: str, role: str) -> str:
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
            logger.debug("User conflict on create, retrying GET", email=email, role=role)
            user_id = await self._get_user(email=email, role=role)
            if user_id is None:
                msg = f"User {email!r} role={role!r} not found after 409 conflict"
                raise RuntimeError(msg)
            return user_id
        response.raise_for_status()
        logger.info("Created user in event-users", email=email, role=role, user_id=response.json()["id"])
        return response.json()["id"]
