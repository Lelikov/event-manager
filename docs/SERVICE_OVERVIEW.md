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

All environment variables are defined in `event_receiver/config.py:97-128` (`Settings` class, Pydantic BaseSettings):

| Variable | Type | Default | Description |
|---|---|---|---|
| `DEBUG` | bool | `False` | Enable debug mode (console log rendering, request logger middleware) |
| `LOG_LEVEL` | str | `"INFO"` | Structlog level |
| `RABBIT_URL` | AmqpDsn | `amqp://guest:guest@localhost:5672/` | RabbitMQ connection URL |
| `RABBIT_EXCHANGE` | str | `"events"` | Topic exchange name |
| `DEFAULT_RABBIT_DESTINATION` | str | `"events.unrouted"` | Fallback routing key when no rule matches |
| `EVENT_ROUTING_RULES` | list[RouteRule] | See `_default_route_rules()` | Ordered routing rules (first match wins) |
| `RABBIT_TOPOLOGY_QUEUES` | list[str] | `[]` (derived from routing destinations) | Explicit queue list for topology declaration |
| `AUTHORIZATION_JWT_VERIFY_KEY` | str | **required** | Shared secret/key for JWT signature verification |
| `AUTHORIZATION_JWT_ALGORITHM` | str | `"HS256"` | JWT algorithm |
| `AUTHORIZATION_JWT_ISSUER` | str | **required** | Expected JWT issuer claim |
| `AUTHORIZATION_JWT_AUDIENCE` | str | **required** | Expected JWT audience claim |
| `EMAIL_API_KEY` | str | **required** | UniSender Go API key (used for HMAC signature validation) |
| `GETSTREAM_API_KEY` | str | **required** | GetStream API key |
| `GETSTREAM_API_SECRET` | str | **required** | GetStream API secret (webhook signature verification) |
| `GETSTREAM_USER_ID_ENCRYPTION_KEY` | str | **required** | AES key for decrypting GetStream user IDs to emails |
| `BOOKING_API_KEY` | str | **required** | API key for booking service authentication |
| `EVENT_USERS_API_URL` | str | **required** | Base URL for event-users service |
| `EVENT_USERS_API_TOKEN` | str | **required** | Bearer token for event-users API |

Source: `event-receiver/event_receiver/config.py:97-128`

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

## Known Limitations (Unfixed Audit Findings)

The following MEDIUM and LOW findings from the 2026-04-19 audit remain as accepted technical debt:

### MEDIUM

| Finding | Location | Impact |
|---|---|---|
| `EVENTS_DIGEST.md` booking.created schema does not match actual code (expects `users[]` list, docs show `user`/`client` objects) | `controllers/ingest.py:148-160` | External callers reading docs will send wrong format |
| `PROJECT_CONTEXT.md` and `CLAUDE.md` document non-existent `/event/cloudevents` endpoint | `routes.py` (absent) | Documentation advertises capability that does not exist |
| `QUEUES_DIGEST.md` shows `*` as source_pattern where code uses `booking` | `config.py:9-94` | Docs mislead on routing behavior |
| CORS wildcard `allow_origins=["*"]` with `allow_credentials=True` | `main.py:108-114` | Invalid per Fetch spec; unclear why CORS is needed for webhook ingress |
| `ingest_jitsi` double-decodes JWT (verify_signature then verify with verify_signature=False) | `controllers/ingest.py:47,64-68` | Architecturally fragile, unnecessary double decode |
| `ingest_booking` mutates `incoming.data` in place via `.pop()` | `controllers/ingest.py:111` | Side-effect on parsed CloudEvent object |
| `pyproject.toml` pins event-schemas via absolute local path | `pyproject.toml:11` | Breaks in CI/Docker/other dev machines |
| `ingest_unisender_go` publishes without event_id or event_time | `controllers/ingest.py:185-193` | CloudEvents spec violation; may cause KeyError in event-saver |
| `normalizers.py` silently swallows ValidationError/KeyError/ValueError | `normalizers.py:45-49` | Schema regressions invisible at runtime |
| `RABBITMQ_MESSAGES_SPEC.md` unreferenced and unverified | Root directory | Potentially stale documentation |

### LOW

| Finding | Location | Impact |
|---|---|---|
| `logger.py` suppresses irrelevant packages (aiokafka, asyncio_redis, botocore) | `logger.py:67-71` | Dead configuration noise |
| Empty `schemas.py` placeholder | `schemas.py` | Module clutter |
| Service name inconsistency (`event-manager` vs `event-receiver`) | `CLAUDE.md:3`, `main.py:105` | Ambiguous naming |
| Bare `Callable` type annotation for getstream decoder | `ioc.py:80,108` | Type checking gap |
| `IngestController` in `Scope.REQUEST` despite no per-request state | `ioc.py:133-145` | Unnecessary allocation overhead per request |
| No automated tests | Project root | Zero test coverage |

### HIGH (Accepted/Partially Addressed)

| Finding | Location | Impact |
|---|---|---|
| No retry/circuit-breaker on event-users HTTP calls in publish path | `adapters/users_client.py:17-48` | Transient event-users failure causes 500 to webhook caller |
| No idempotency enforcement at event-receiver layer | `adapters/publisher.py:67-71` | Duplicate webhooks produce duplicate RabbitMQ messages (dedup delegated to event-saver) |
