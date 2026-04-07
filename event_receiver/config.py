from pydantic import AmqpDsn, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from event_receiver.routing import RouteRule, RoutingConfig


def _default_route_rules() -> list[RouteRule]:
    return [
        RouteRule(
            destination="events.booking.lifecycle",
            source_pattern="booking",
            type_pattern="booking.created",
        ),
        RouteRule(
            destination="events.booking.lifecycle",
            source_pattern="booking",
            type_pattern="booking.rescheduled",
        ),
        RouteRule(
            destination="events.booking.lifecycle",
            source_pattern="booking",
            type_pattern="booking.reassigned",
        ),
        RouteRule(
            destination="events.booking.lifecycle",
            source_pattern="booking",
            type_pattern="booking.cancelled",
        ),
        RouteRule(
            destination="events.booking.reminder",
            source_pattern="booking",
            type_pattern="booking.reminder_sent",
        ),
        RouteRule(
            destination="events.chat.lifecycle",
            source_pattern="booking",
            type_pattern="chat.created",
        ),
        RouteRule(
            destination="events.chat.lifecycle",
            source_pattern="booking",
            type_pattern="chat.deleted",
        ),
        RouteRule(
            destination="events.chat.activity",
            source_pattern="booking",
            type_pattern="chat.message_sent",
        ),
        RouteRule(
            destination="events.meeting.lifecycle",
            source_pattern="booking",
            type_pattern="meeting.url_created",
        ),
        RouteRule(
            destination="events.meeting.lifecycle",
            source_pattern="booking",
            type_pattern="meeting.url_deleted",
        ),
        RouteRule(
            destination="events.notification.delivery",
            source_pattern="booking",
            type_pattern="notification.email.message_sent",
        ),
        RouteRule(
            destination="events.notification.delivery",
            source_pattern="booking",
            type_pattern="notification.telegram.message_sent",
        ),
        RouteRule(
            destination="events.jitsi",
            source_pattern="jitsi*",
            type_pattern="*",
        ),
        RouteRule(
            destination="events.mail",
            source_pattern="unisender-go",
            type_pattern="unisender.*",
        ),
        RouteRule(
            destination="events.chat",
            source_pattern="getstream",
            type_pattern="getstream.*",
        ),
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
    rabbit_topology_queues: list[str] = Field(default_factory=list)

    authorization_jwt_verify_key: str = Field(strict=True)
    authorization_jwt_algorithm: str = "HS256"
    authorization_jwt_issuer: str = Field(strict=True)
    authorization_jwt_audience: str = Field(strict=True)

    email_api_key: str = Field(strict=True)

    getstream_api_key: str = Field(strict=True)
    getstream_api_secret: str = Field(strict=True)
    getstream_user_id_encryption_key: str = Field(strict=True)

    booking_api_key: str = Field(strict=True)

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
