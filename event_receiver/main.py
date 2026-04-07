import json
import time
from contextlib import asynccontextmanager
from logging import getLevelNamesMapping
from pathlib import Path
from typing import TYPE_CHECKING

import anyio
import structlog
from dishka import make_async_container
from dishka.integrations.fastapi import FastapiProvider, setup_dishka
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from faststream.rabbit import RabbitBroker
from starlette.middleware.base import BaseHTTPMiddleware

from event_receiver.config import Settings
from event_receiver.interfaces.publisher import ITopologyManager
from event_receiver.ioc import AppProvider
from event_receiver.logger import setup_logger
from event_receiver.routes import root_router


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from starlette.middleware.base import RequestResponseEndpoint
    from starlette.requests import Request
    from starlette.responses import Response


_REQUEST_LOG_FILE = Path("incoming_requests.jsonl")


class RequestLoggerMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method == "POST":
            body = await request.body()
            try:
                body_data = json.loads(body)
            except json.JSONDecodeError, ValueError:
                body_data = body.decode(errors="replace")

            record = {
                "ts": time.time(),
                "path": request.url.path,
                "headers": request.headers,
                "body": body_data,
            }
            async with await anyio.open_file(_REQUEST_LOG_FILE, "a") as f:
                await f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

        return await call_next(request)


container = make_async_container(AppProvider(), FastapiProvider())
logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
    settings = await container.get(Settings)
    log_level = getLevelNamesMapping().get(settings.log_level)
    setup_logger(log_level=log_level, console_render=settings.debug)

    logger.info(
        "Starting event receiver application",
        log_level=settings.log_level,
        debug=settings.debug,
        rabbit_exchange=settings.rabbit_exchange,
    )

    broker = await container.get(RabbitBroker)
    await broker.connect()
    logger.info("Connected to RabbitMQ broker")

    topology_manager = await container.get(ITopologyManager)
    await topology_manager.ensure_topology()
    logger.info("RabbitMQ topology ensured and application is ready")

    yield

    logger.info("Shutting down event receiver application")
    await broker.stop()
    await container.close()
    logger.info("Event receiver application shutdown complete")


app = FastAPI(title="event-manager", version="0.1.0", lifespan=lifespan)
setup_dishka(container=container, app=app)
app.include_router(root_router)

app.add_middleware(RequestLoggerMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
