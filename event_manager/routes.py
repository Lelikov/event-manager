from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, HTTPException, Request, status

from event_manager.config import Settings
from event_manager.errors import BadRequestError, ConfigurationError, IngestError, UnauthorizedError
from event_manager.interfaces.ingest import IIngestController
from event_manager.schemas import FreeFormIngestRequest


root_router = APIRouter(route_class=DishkaRoute)


def _raise_http_from_ingest_error(exc: IngestError) -> None:
    if isinstance(exc, BadRequestError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if isinstance(exc, UnauthorizedError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    if isinstance(exc, ConfigurationError):
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal ingest error") from exc


@root_router.post("/event/cloudevents", status_code=status.HTTP_202_ACCEPTED)
async def ingest_event(request: Request, ingest_controller: FromDishka[IIngestController]) -> None:
    try:
        await ingest_controller.ingest_cloudevent(headers=request.headers, body=await request.body())
    except IngestError as exc:
        _raise_http_from_ingest_error(exc)


@root_router.post("/ingest/frontend", status_code=status.HTTP_202_ACCEPTED)
async def ingest_frontend_event(
    payload: FreeFormIngestRequest,
    request: Request,
    settings: FromDishka[Settings],
    ingest_controller: FromDishka[IIngestController],
) -> None:
    token = request.headers.get(settings.frontend_jwt_header)
    try:
        await ingest_controller.ingest_frontend(payload=payload, token=token)
    except IngestError as exc:
        _raise_http_from_ingest_error(exc)


@root_router.post("/ingest/backend", status_code=status.HTTP_202_ACCEPTED)
async def ingest_backend_event(request: Request, ingest_controller: FromDishka[IIngestController]) -> None:
    try:
        await ingest_controller.ingest_backend(headers=request.headers, body=await request.body())
    except IngestError as exc:
        _raise_http_from_ingest_error(exc)


@root_router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
