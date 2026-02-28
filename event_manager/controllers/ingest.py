import json
from collections.abc import Mapping
from contextlib import suppress

from cloudevents.pydantic import from_http

from event_manager.config import Settings
from event_manager.errors import BadRequestError, ConfigurationError, UnauthorizedError
from event_manager.interfaces.ingest import IIngestController
from event_manager.interfaces.publisher import ICloudEventPublisher
from event_manager.interfaces.security import IBackendSignatureVerifier, IFrontendJWTVerifier
from event_manager.schemas import FreeFormIngestRequest


class IngestController(IIngestController):
    def __init__(
        self,
        *,
        settings: Settings,
        publisher: ICloudEventPublisher,
        frontend_jwt_verifier: IFrontendJWTVerifier,
        backend_signature_verifier: IBackendSignatureVerifier,
    ) -> None:
        self._settings = settings
        self._publisher = publisher
        self._frontend_jwt_verifier = frontend_jwt_verifier
        self._backend_signature_verifier = backend_signature_verifier

    async def ingest_cloudevent(self, *, headers: Mapping[str, str], body: bytes) -> None:
        try:
            incoming = from_http(headers=headers, data=body)
        except Exception as exc:
            raise BadRequestError("Invalid CloudEvent payload or headers") from exc

        await self._publisher.publish(
            source=str(incoming.source),
            event_type=incoming.type,
            data=incoming.data,
            event_id=str(incoming.id),
            event_time=str(incoming.time) if incoming.time else None,
        )

    async def ingest_frontend(self, *, payload: FreeFormIngestRequest, token: str | None) -> None:
        source = payload.source or self._settings.frontend_source
        event_type = payload.type or self._settings.frontend_type

        if not token:
            raise UnauthorizedError(f"Missing JWT header: {self._settings.frontend_jwt_header}")

        try:
            self._frontend_jwt_verifier.verify(
                token=token,
                payload=payload.payload,
                source=source,
                event_type=event_type,
                require_payload_digest=True,
            )
        except Exception as exc:
            raise UnauthorizedError("Invalid frontend JWT or payload integrity mismatch") from exc

        await self._publisher.publish(
            source=source,
            event_type=event_type,
            data=payload.payload,
        )

    async def ingest_backend(self, *, headers: Mapping[str, str], body: bytes) -> None:
        if not body:
            raise BadRequestError("Empty request body")

        signature = headers.get(self._settings.backend_signature_header)
        if not signature:
            raise UnauthorizedError(f"Missing signature header: {self._settings.backend_signature_header}")

        try:
            is_valid_signature = self._backend_signature_verifier.verify(body=body, signature_header=signature)
        except Exception as exc:
            raise ConfigurationError("Invalid backend signature verifier configuration") from exc
        if not is_valid_signature:
            raise UnauthorizedError("Invalid backend signature")

        source = self._settings.backend_source
        event_type = self._settings.backend_type
        event_id: str | None = None
        event_time: str | None = None

        incoming = None
        with suppress(Exception):
            incoming = from_http(headers=headers, data=body)

        if incoming is not None:
            source = str(incoming.source)
            event_type = incoming.type
            event_id = str(incoming.id)
            event_time = str(incoming.time) if incoming.time else None
            data = incoming.data if isinstance(incoming.data, dict) else {"value": incoming.data}
        else:
            try:
                parsed = json.loads(body)
            except json.JSONDecodeError as exc:
                raise BadRequestError(
                    "Unsupported backend payload format. Use CloudEvents or JSON object.",
                ) from exc
            if not isinstance(parsed, dict):
                raise BadRequestError("Unsupported backend payload format. JSON payload must be an object.")

            data = parsed
            source = str(parsed.get("source", source))
            event_type = str(parsed.get("type", event_type))

        await self._publisher.publish(
            source=source,
            event_type=event_type,
            data=data,
            event_id=event_id,
            event_time=event_time,
        )
