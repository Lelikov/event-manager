from dishka import Provider, Scope, provide
from faststream.rabbit import ExchangeType, RabbitBroker, RabbitExchange, fastapi

from event_manager.adapters import CloudEventPublisher, RabbitTopologyManager
from event_manager.config import Settings
from event_manager.controllers import IngestController
from event_manager.interfaces.ingest import IIngestController
from event_manager.interfaces.publisher import ICloudEventPublisher, ITopologyManager
from event_manager.interfaces.routing import IEventRouter
from event_manager.interfaces.security import IBackendSignatureVerifier, IFrontendJWTVerifier
from event_manager.routing import EventRouter
from event_manager.security import (
    BackendSignatureConfig,
    BackendSignatureVerifier,
    FrontendJWTConfig,
    FrontendJWTVerifier,
)


class AppProvider(Provider):
    @provide(scope=Scope.APP)
    def provide_settings(self) -> Settings:
        return Settings()

    @provide(scope=Scope.APP)
    def provide_faststream_router(self, settings: Settings) -> fastapi.RabbitRouter:
        return fastapi.RabbitRouter(settings.rabbit_url)

    @provide(scope=Scope.APP)
    def provide_broker(self, router: fastapi.RabbitRouter) -> RabbitBroker:
        return router.broker

    @provide(scope=Scope.APP)
    def provide_exchange(self, settings: Settings) -> RabbitExchange:
        return RabbitExchange(
            name=settings.rabbit_exchange,
            type=ExchangeType.TOPIC,
            durable=True,
        )

    @provide(scope=Scope.APP)
    def provide_event_router(self, settings: Settings) -> IEventRouter:
        return EventRouter(settings.routing)

    @provide(scope=Scope.APP)
    def provide_frontend_jwt_verifier(self, settings: Settings) -> IFrontendJWTVerifier:
        return FrontendJWTVerifier(
            FrontendJWTConfig(
                verify_key=settings.frontend_jwt_verify_key,
                algorithm=settings.frontend_jwt_algorithm,
                issuer=settings.frontend_jwt_issuer,
                audience=settings.frontend_jwt_audience,
            ),
        )

    @provide(scope=Scope.APP)
    def provide_backend_signature_verifier(self, settings: Settings) -> IBackendSignatureVerifier:
        return BackendSignatureVerifier(
            BackendSignatureConfig(
                secret=settings.backend_signature_secret,
                algorithm=settings.backend_signature_algorithm,
            ),
        )

    @provide(scope=Scope.APP)
    def provide_publisher(
        self,
        broker: RabbitBroker,
        exchange: RabbitExchange,
        event_router: IEventRouter,
    ) -> ICloudEventPublisher:
        return CloudEventPublisher(
            broker=broker,
            exchange=exchange,
            router_by_event=event_router,
        )

    @provide(scope=Scope.APP)
    def provide_topology_manager(
        self,
        settings: Settings,
        broker: RabbitBroker,
        exchange: RabbitExchange,
    ) -> ITopologyManager:
        return RabbitTopologyManager(
            broker=broker,
            exchange=exchange,
            topology_queues=settings.topology_queues,
        )

    @provide(scope=Scope.REQUEST)
    def provide_ingest_controller(
        self,
        settings: Settings,
        publisher: ICloudEventPublisher,
        frontend_jwt_verifier: IFrontendJWTVerifier,
        backend_signature_verifier: IBackendSignatureVerifier,
    ) -> IIngestController:
        return IngestController(
            settings=settings,
            publisher=publisher,
            frontend_jwt_verifier=frontend_jwt_verifier,
            backend_signature_verifier=backend_signature_verifier,
        )
