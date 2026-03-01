from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from event_manager.routing import RouteRule, RoutingConfig


def _default_route_rules() -> list[RouteRule]:
    return [
        RouteRule(
            destination="events.user",
            source_pattern="urn:svc:user-*",
            type_pattern="user.*",
        ),
        RouteRule(
            destination="events.billing",
            source_pattern="urn:svc:billing",
            type_pattern="billing.*",
        ),
        RouteRule(
            destination="events.mail",
            source_pattern="unisender-go",
            type_pattern="unisender.*",
        ),
    ]


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    rabbit_url: str = "amqp://guest:guest@localhost:5672/"
    rabbit_exchange: str = "events"
    default_rabbit_destination: str = "events.unrouted"
    event_routing_rules: list[RouteRule] = Field(default_factory=_default_route_rules)
    rabbit_topology_queues: list[str] = Field(default_factory=list)

    authorization_jwt_verify_key: str = "dev-authorization-jwt-secret"
    authorization_jwt_algorithm: str = "HS256"
    authorization_jwt_issuer: str = "event-manager"
    authorization_jwt_audience: str = "event-manager-ingest"

    email_api_key: str = "dev-unisender-go-api-key"

    backend_source: str = "urn:ingress:backend"
    backend_type: str = "backend.event"

    @property
    def routing_destinations(self) -> set[str]:
        destinations = {self.default_rabbit_destination}
        destinations.update(rule.destination for rule in self.event_routing_rules)
        return destinations

    @property
    def topology_queues(self) -> set[str]:
        explicit = set(self.rabbit_topology_queues)
        return explicit or self.routing_destinations

    @property
    def routing(self) -> RoutingConfig:
        return RoutingConfig(
            default_destination=self.default_rabbit_destination,
            rules=self.event_routing_rules,
        )


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    return AppSettings()


Settings = AppSettings
