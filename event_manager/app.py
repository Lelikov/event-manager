from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from dishka import make_async_container
from dishka.integrations.fastapi import FastapiProvider, setup_dishka
from fastapi import FastAPI
from faststream.rabbit import RabbitBroker

from event_manager.interfaces.publisher import ITopologyManager
from event_manager.ioc import AppProvider
from event_manager.routes import root_router


container = make_async_container(AppProvider(), FastapiProvider())


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
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
