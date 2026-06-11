from logging import getLevelNamesMapping

import structlog
from event_schemas.queues import ROUTING_RULES
from pydantic import AmqpDsn, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from event_receiver.routing import RouteRule, RoutingConfig


logger = structlog.get_logger(__name__)

MIN_SECRET_LENGTH = 16

_SECRET_FIELDS = (
    "authorization_jwt_verify_key",
    "email_api_key",
    "getstream_api_key",
    "getstream_api_secret",
    "getstream_user_id_encryption_key",
    "booking_api_key",
    "admin_api_key",
    "calcom_webhook_secret",
    "event_users_api_token",
)

_PLACEHOLDER_MARKERS = ("change", "placeholder", "dev-token", "example", "secret-here")


def _default_route_rules() -> list[RouteRule]:
    """Build default routing rules from the canonical event_schemas table."""
    return [
        RouteRule(
            destination=str(rule.destination),
            source_pattern=rule.source_pattern,
            type_pattern=rule.type_pattern,
        )
        for rule in ROUTING_RULES
    ]


def _is_weak_secret(value: str) -> bool:
    if len(value) < MIN_SECRET_LENGTH:
        return True
    if len(set(value)) == 1:
        return True
    lowered = value.lower()
    return any(marker in lowered for marker in _PLACEHOLDER_MARKERS)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    debug: bool = False
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:5173"

    rabbit_url: AmqpDsn = "amqp://guest:guest@localhost:5672/"
    rabbit_exchange: str = "events"
    publish_timeout: float = 10.0
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
    calcom_webhook_secret: str = Field(strict=True)

    event_users_api_url: str = Field(strict=True)
    event_users_api_token: str = Field(strict=True)

    @model_validator(mode="after")
    def _validate_secret_strength(self) -> Settings:
        """Reject weak or placeholder secrets outside debug mode (fail fast at startup)."""
        if self.debug:
            return self
        weak = [name for name in _SECRET_FIELDS if _is_weak_secret(getattr(self, name))]
        if weak:
            raise ValueError(
                f"Weak or placeholder secrets are not allowed outside DEBUG "
                f"(min {MIN_SECRET_LENGTH} chars, no placeholders): {', '.join(weak)}",
            )
        return self

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalize_log_level(cls, value: object) -> str:
        normalized = str(value).strip().upper()
        if normalized in getLevelNamesMapping():
            return normalized
        logger.warning("Unknown LOG_LEVEL value, defaulting to INFO", log_level=value)
        return "INFO"

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

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
