from event_schemas.queues import ROUTING_RULES
from pydantic import AmqpDsn, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from event_receiver.routing import RouteRule, RoutingConfig


def _default_route_rules() -> list[RouteRule]:
    """Default routing rules generated from the canonical event_schemas table."""
    return [
        RouteRule(
            destination=str(rule.destination),
            source_pattern=rule.source_pattern,
            type_pattern=rule.type_pattern,
        )
        for rule in ROUTING_RULES
    ]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    debug: bool = False
    log_level: str = "INFO"

    rabbit_url: AmqpDsn = "amqp://guest:guest@localhost:5672/"
    rabbit_exchange: str = "events"
    default_rabbit_destination: str = "events.unrouted"
    event_routing_rules: list[RouteRule] = Field(default_factory=_default_route_rules)

    authorization_jwt_verify_key: str = Field(strict=True)
    authorization_jwt_algorithm: str = "HS256"
    authorization_jwt_issuer: str = Field(strict=True)
    authorization_jwt_audience: str = Field(strict=True)

    email_api_key: str = Field(strict=True)

    getstream_api_key: str = Field(strict=True)
    getstream_api_secret: str = Field(strict=True)
    getstream_user_id_encryption_key: str = Field(strict=True)

    booking_api_key: str = Field(strict=True)
    admin_api_key: str = Field(strict=True)

    event_users_api_url: str = Field(strict=True)
    event_users_api_token: str = Field(strict=True)

    @property
    def routing_destinations(self) -> set[str]:
        destinations = {self.default_rabbit_destination}
        destinations.update(rule.destination for rule in self.event_routing_rules)
        return destinations

    @property
    def routing(self) -> RoutingConfig:
        return RoutingConfig(
            default_destination=self.default_rabbit_destination,
            rules=self.event_routing_rules,
        )
