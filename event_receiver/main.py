from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from logging import getLevelNamesMapping

from dishka import make_async_container
from dishka.integrations.fastapi import FastapiProvider, setup_dishka
from fastapi import FastAPI
from faststream.rabbit import RabbitBroker

from event_receiver.config import Settings
from event_receiver.interfaces.publisher import ITopologyManager
from event_receiver.ioc import AppProvider
from event_receiver.logger import setup_logger
from event_receiver.routes import root_router


container = make_async_container(AppProvider(), FastapiProvider())


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
    settings = await container.get(Settings)
    log_level = getLevelNamesMapping().get(settings.log_level)
    setup_logger(log_level=log_level, console_render=settings.debug)
    broker = await container.get(RabbitBroker)
    await broker.connect()
    topology_manager = await container.get(ITopologyManager)
    await topology_manager.ensure_topology()
    yield
    await broker.stop()
    await container.close()


app = FastAPI(title="event-manager", version="0.1.0", lifespan=lifespan)
setup_dishka(container=container, app=app)
app.include_router(root_router)
