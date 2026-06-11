# event-receiver Service Overview

## Business Domain

The `event-receiver` service is the HTTP ingress gateway for the event-driven system. It accepts webhook callbacks and API calls from external systems, validates authentication/integrity, normalizes payloads into CloudEvents binary format, and publishes them to RabbitMQ for downstream processing.

## Responsibilities

- Accept HTTP POST requests from four external sources: booking service, Jitsi, UniSender Go, and GetStream
- Authenticate each request using source-specific mechanisms (API key, JWT, HMAC signature, webhook signature)
- Parse and validate incoming payloads against expected schemas
- Transform raw payloads into normalized CloudEvent format with participant extraction
- Resolve participant emails to user IDs via the `event-users` service
- Route events to correct RabbitMQ queues based on configurable glob-pattern rules
- Declare and maintain RabbitMQ topology (exchanges, queues, DLQs, bindings) on startup
- Generate distributed tracing IDs (trace_id, span_id) and idempotency keys for each published event
- Provide a health check endpoint for container orchestration

## NOT Responsible For

- Persisting events to any database (no DB connection)
- Consuming messages from RabbitMQ (publish-only)
- Processing or acting on events after publication
- Direct service-to-service HTTP calls except to `event-users` for user ID resolution
- Schema migrations or data modeling
- Deduplication enforcement (delegated to `event-saver` via DB constraints)
- Sending notifications, emails, or chat messages

## Runtime Dependencies

| Dependency | Type | Purpose |
|---|---|---|
| RabbitMQ | Message broker | Publish CloudEvents to topic exchange |
| event-users | HTTP service | Resolve participant emails to user UUIDs (`/api/users/roles/{role}/emails/{email}`, `/api/users`) |
| event-schemas | Python library | Shared Pydantic models for event payloads, EventType enum, priorities, schema versions |

## Key Configuration

All environment variables are defined in `event_receiver/config.py` (`Settings` class, Pydantic BaseSettings):

| Variable | Type | Default | Description |
|---|---|---|---|
| `DEBUG` | bool | `False` | Enable debug mode (console log rendering, request logger middleware) |
| `LOG_LEVEL` | str | `"INFO"` | Structlog level |
| `RABBIT_URL` | AmqpDsn | `amqp://guest:guest@localhost:5672/` | RabbitMQ connection URL |
| `RABBIT_EXCHANGE` | str | `"events"` | Topic exchange name |
| `DEFAULT_RABBIT_DESTINATION` | str | `"events.unrouted"` | Fallback routing key when no rule matches |
| `EVENT_ROUTING_RULES` | list[RouteRule] | See `_default_route_rules()` | Ordered routing rules (first match wins) |
| `PUBLISH_TIMEOUT` | float | `10.0` | RabbitMQ publish confirm timeout (seconds); exceeded → HTTP 503 |
| `CORS_ORIGINS` | str | `http://localhost:5173` | Comma-separated CORS allow-origins (flows through Settings/.env) |
| `AUTHORIZATION_JWT_VERIFY_KEY` | str | **required** | Shared secret/key for JWT signature verification |
| `AUTHORIZATION_JWT_ALGORITHM` | str | `"HS256"` | JWT algorithm |
| `AUTHORIZATION_JWT_ISSUER` | str | **required** | Expected JWT issuer claim |
| `AUTHORIZATION_JWT_AUDIENCE` | str | **required** | Expected JWT audience claim |
| `EMAIL_API_KEY` | str | **required** | UniSender Go API key (used for HMAC signature validation) |
| `GETSTREAM_API_KEY` | str | **required** | GetStream API key |
| `GETSTREAM_API_SECRET` | str | **required** | GetStream API secret (webhook signature verification) |
| `GETSTREAM_USER_ID_ENCRYPTION_KEY` | str | **required** | AES key for decrypting GetStream user IDs to emails |
| `BOOKING_API_KEY` | str | **required** | API key for booking service authentication |
| `ADMIN_API_KEY` | str | **required** | API key for /event/admin authentication |
| `CALCOM_WEBHOOK_SECRET` | str | **required** | Secret for cal.com `X-Cal-Signature-256` HMAC verification |
| `EVENT_USERS_API_URL` | str | **required** | Base URL for event-users service |
| `EVENT_USERS_API_TOKEN` | str | **required** | Bearer token for event-users API |

Outside `DEBUG`, every secret must be ≥16 chars, non-placeholder (startup fails fast otherwise).

Source: `event-receiver/event_receiver/config.py` (`Settings`)

## Architecture Diagram

```mermaid
graph TD
    subgraph "External Sources"
        BOOKING[Booking Service]
        JITSI[Jitsi Webhooks]
        UNISENDER[UniSender Go]
        GETSTREAM[GetStream]
    end

    subgraph "event-receiver"
        ROUTES[routes.py<br/>HTTP Layer]
        INGEST[IngestController<br/>Validation & Orchestration]
        SECURITY[AuthorizationJWTVerifier<br/>JWT/Signature Auth]
        PUBLISHER[CloudEventPublisher<br/>CloudEvent Builder]
        NORMALIZER[normalizers.py<br/>Payload Normalization]
        ROUTER[EventRouter<br/>Routing Rules]
        TOPOLOGY[RabbitTopologyManager<br/>Queue Declaration]
        USER_RESOLVER[UserResolver<br/>HTTP Client]
    end

    subgraph "Infrastructure"
        RABBIT[RabbitMQ<br/>Topic Exchange]
        EVENT_USERS[event-users Service]
    end

    BOOKING -->|POST /event/booking| ROUTES
    JITSI -->|POST /event/jitsi| ROUTES
    UNISENDER -->|POST /event/unisender-go| ROUTES
    GETSTREAM -->|POST /event/getstream| ROUTES

    ROUTES --> INGEST
    INGEST --> SECURITY
    INGEST --> PUBLISHER
    PUBLISHER --> NORMALIZER
    PUBLISHER --> ROUTER
    PUBLISHER --> USER_RESOLVER
    USER_RESOLVER -->|HTTP GET/POST| EVENT_USERS
    TOPOLOGY -->|Declare on startup| RABBIT
    PUBLISHER -->|Publish CloudEvent| RABBIT
```

## Known Limitations (audit-v2, 2026-06-11)

All findings of the 2026-06-11 audit were fixed (see `docs/AUDIT.md`). Remaining accepted limitations:

| Limitation | Detail |
|---|---|
| DLQ 24h loss window | `*.dlq` queues have `x-message-ttl=86400000` and no consumer; messages are destroyed after 24h. Queue args are canonical in `event_schemas.queues` (CONTRACT_DECISIONS D2) — requires alerting on DLQ depth + manual redrive runbook (see `QUEUES_DIGEST.md`) |
| user_id backfill | When event-users is down, events publish with `user_id=null` participants; the receiver logs a structured warning but the backfill job is downstream (event-saver/event-users) scope |
| GetStream user-id legacy CBC fallback | Decoder accepts both AES-GCM (canonical) and legacy zero-IV AES-CBC; the CBC fallback should be deleted once event-booking switches its encoder to `encode_getstream_user_id` (AES-GCM) |
| In-memory idempotency cache | Duplicate suppression is per-process with 10-minute TTL; authoritative dedup remains event-saver's DB constraint |
