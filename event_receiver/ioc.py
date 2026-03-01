import structlog
from dishka import Provider, Scope, provide
from faststream.rabbit import ExchangeType, RabbitBroker, RabbitExchange, fastapi

from event_receiver.adapters import CloudEventPublisher, RabbitTopologyManager
from event_receiver.config import Settings
from event_receiver.controllers import IngestController
from event_receiver.interfaces.ingest import IIngestController
from event_receiver.interfaces.publisher import ICloudEventPublisher, ITopologyManager
from event_receiver.interfaces.routing import IEventRouter
from event_receiver.interfaces.security import IAuthorizationJWTVerifier
from event_receiver.routing import EventRouter
from event_receiver.security import AuthorizationJWTConfig, AuthorizationJWTVerifier


logger = structlog.get_logger(__name__)


class AppProvider(Provider):
    @provide(scope=Scope.APP)
    def provide_settings(self) -> Settings:
        settings = Settings()
        logger.info(
            "Settings initialized",
            debug=settings.debug,
            log_level=settings.log_level,
            rabbit_exchange=settings.rabbit_exchange,
            routing_rules_count=len(settings.event_routing_rules),
        )
        return settings

    @provide(scope=Scope.APP)
    def provide_faststream_router(self, settings: Settings) -> fastapi.RabbitRouter:
        logger.info("Creating FastStream RabbitRouter", rabbit_url=settings.rabbit_url)
        return fastapi.RabbitRouter(settings.rabbit_url)

    @provide(scope=Scope.APP)
    def provide_broker(self, router: fastapi.RabbitRouter) -> RabbitBroker:
        logger.info("Providing RabbitBroker from FastStream router")
        return router.broker

    @provide(scope=Scope.APP)
    def provide_exchange(self, settings: Settings) -> RabbitExchange:
        logger.info("Creating RabbitExchange", exchange=settings.rabbit_exchange)
        return RabbitExchange(
            name=settings.rabbit_exchange,
            type=ExchangeType.TOPIC,
            durable=True,
        )

    @provide(scope=Scope.APP)
    def provide_event_router(self, settings: Settings) -> IEventRouter:
        logger.info("Providing EventRouter")
        return EventRouter(settings.routing)

    @provide(scope=Scope.APP)
    def provide_authorization_jwt_verifier(self, settings: Settings) -> IAuthorizationJWTVerifier:
        logger.info(
            "Providing AuthorizationJWTVerifier",
            jwt_algorithm=settings.authorization_jwt_algorithm,
            jwt_issuer=settings.authorization_jwt_issuer,
            jwt_audience=settings.authorization_jwt_audience,
        )
        return AuthorizationJWTVerifier(
            AuthorizationJWTConfig(
                verify_key=settings.authorization_jwt_verify_key,
                algorithm=settings.authorization_jwt_algorithm,
                issuer=settings.authorization_jwt_issuer,
                audience=settings.authorization_jwt_audience,
            ),
        )

    @provide(scope=Scope.APP)
    def provide_publisher(
        self,
        broker: RabbitBroker,
        exchange: RabbitExchange,
        event_router: IEventRouter,
    ) -> ICloudEventPublisher:
        logger.info("Providing CloudEventPublisher")
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
        logger.info("Providing RabbitTopologyManager", topology_queue_count=len(settings.topology_queues))
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
        authorization_jwt_verifier: IAuthorizationJWTVerifier,
    ) -> IIngestController:
        logger.debug("Providing IngestController for request scope")
        return IngestController(
            settings=settings,
            publisher=publisher,
            authorization_jwt_verifier=authorization_jwt_verifier,
        )
