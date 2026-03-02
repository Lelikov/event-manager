from typing import Any

import structlog
from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse

from event_receiver.errors import BadRequestError, ConfigurationError, IngestError, UnauthorizedError
from event_receiver.interfaces.ingest import IIngestController


root_router = APIRouter(route_class=DishkaRoute)
logger = structlog.get_logger(__name__)

INGEST_ROUTE_TO_METHOD = {
    "/event/cloudevents": "ingest_cloudevent",
    "/event/unisender-go": "ingest_unisender_go",
    "/event/getstream": "ingest_getstream",
}


def _raise_http_from_ingest_error(exc: IngestError) -> None:
    if isinstance(exc, BadRequestError):
        logger.warning("Ingest request failed with bad request", error=str(exc))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if isinstance(exc, UnauthorizedError):
        logger.warning("Ingest request failed with unauthorized error", error=str(exc))
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    if isinstance(exc, ConfigurationError):
        logger.error("Ingest request failed with configuration error", error=str(exc))
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    logger.exception("Ingest request failed with unexpected internal error")
    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal ingest error") from exc


async def _handle_ingest_request(
    request: Request,
    ingest_controller: IIngestController,
    controller_method_name: str,
) -> Any:
    if request.method == "GET":
        logger.debug("Received GET on ingest endpoint", path=request.url.path)
        return JSONResponse(status_code=status.HTTP_200_OK, content={"status": "ok"})

    logger.info("Received ingest request", path=request.url.path, method=request.method)

    try:
        controller_method = getattr(ingest_controller, controller_method_name)
        await controller_method(headers=request.headers, body=await request.body())
        logger.info("Ingest request accepted", path=request.url.path, controller_method=controller_method_name)
    except IngestError as exc:
        _raise_http_from_ingest_error(exc)

    return None


def _register_ingest_routes() -> None:
    for route_path, controller_method_name in INGEST_ROUTE_TO_METHOD.items():

        async def ingest_endpoint(
            request: Request,
            ingest_controller: FromDishka[IIngestController],
            _controller_method_name: str = controller_method_name,
        ) -> Any:
            return await _handle_ingest_request(
                request=request,
                ingest_controller=ingest_controller,
                controller_method_name=_controller_method_name,
            )

        root_router.add_api_route(
            route_path,
            ingest_endpoint,
            methods=["POST", "GET"],
            status_code=status.HTTP_202_ACCEPTED,
            response_model=None,
        )


_register_ingest_routes()


@root_router.get("/health")
async def health() -> dict[str, str]:
    logger.debug("Health check requested")
    return {"status": "ok"}
