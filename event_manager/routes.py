from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, HTTPException, Request, status

from event_manager.errors import BadRequestError, ConfigurationError, IngestError, UnauthorizedError
from event_manager.interfaces.ingest import IIngestController


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


@root_router.post("/event/unisender-go", status_code=status.HTTP_202_ACCEPTED)
async def ingest_unisender_go_event(request: Request, ingest_controller: FromDishka[IIngestController]) -> None:
    try:
        await ingest_controller.ingest_unisender_go(headers=request.headers, body=await request.body())
    except IngestError as exc:
        _raise_http_from_ingest_error(exc)


@root_router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
