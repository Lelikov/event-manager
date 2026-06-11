# event-receiver Dependencies

## What This Service Needs From Other Services

### RabbitMQ (Message Broker)

| Aspect | Detail |
|---|---|
| Connection | `Settings.rabbit_url` (default: `amqp://guest:guest@localhost:5672/`) |
| Exchange | Topic exchange named per `Settings.rabbit_exchange` (default: `events`), durable |
| Startup requirement | Must be available within 3 connection attempts (2s backoff) or service fails to start |
| Runtime requirement | Must be available for every event publish; no buffering or retry on publish failure |
| Source | `main.py:77-90` (connect with retry), `adapters/publisher.py:115-123` (publish) |

**If RabbitMQ is unavailable**:
- At startup: Service fails after 3 retries, pod enters crash loop
- At runtime: `broker.publish()` raises, causing HTTP 500 to webhook caller; event is lost (no local buffering)

---

### event-users (HTTP Service)

| Aspect | Detail |
|---|---|
| Base URL | `Settings.event_users_api_url` |
| Authentication | Bearer token via `Settings.event_users_api_token` |
| Timeout | 10 seconds per request (`ioc.py:93`) |
| Retry policy | `tenacity` retry with exponential backoff, up to 3 attempts on `httpx.HTTPStatusError` or `httpx.TimeoutException` |
| Endpoints called | `GET /api/users/by-identity?email=&role=` (lookup), `POST /api/users` (create) |
| Call frequency | Once per participant per event (can be 1-2 calls per event typically) |
| Source | `adapters/users_client.py:17-49` |

**If event-users is unavailable**:
- Calls are retried up to 3 times with exponential backoff
- After retries are exhausted, `UserResolver` falls back gracefully to `user_id=None`
- Event is still published to RabbitMQ; participant entries will have `user_id=null`
- The publisher emits a structured WARNING (`unresolved_emails`, `unresolved_count`) for every
  publish with unresolved user_ids — alert on it; no automatic backfill exists downstream
- Webhook caller receives `202 Accepted` (event-users failure is no longer in the critical path)

---

### event-schemas (Python Library)

| Aspect | Detail |
|---|---|
| Import | `from event_schemas import ...` (Pydantic models, EventType enum, priorities) |
| Used for | Payload validation (`BookingCreatedPayload`), event type enum, priority mapping, schema versions |
| Source | `controllers/ingest.py:9-10`, `adapters/publisher.py:5`, `normalizers.py:10-17` |

**If event-schemas is incompatible**:
- Import errors at module load time (service won't start)
- Validation failures at runtime if payload models change

---

## What This Service Provides To Others

### To Downstream Consumers (via RabbitMQ)

| Consumer | What it receives | Queue(s) |
|---|---|---|
| event-saver | All normalized CloudEvents with participant user_ids | All queues in topology |
| (future consumers) | Events on any declared queue | Per routing rules |

**Contract**: CloudEvents binary format with normalized body structure:
```json
{
  "original": { /* raw payload */ },
  "normalized": {
    "participants": [
      {"email": "...", "role": "...", "user_id": "uuid"}
    ]
  }
}
```

CloudEvent extension attributes: `traceid`, `spanid`, `idempotencykey`, `booking_id`, `dataschema`, `publisherservice`, `publisherversion`.

Source: `adapters/publisher.py:93-123`

---

### To Webhook Sources (HTTP Response)

| Source | Provided response | Contract |
|---|---|---|
| Booking service | `202 Accepted` on success | Must respond within reasonable timeout |
| cal.com | `202 Accepted` on success; `503` on broker failure (cal.com retries) | `X-Cal-Signature-256` HMAC-SHA256 |
| Jitsi | `202 Accepted` on success | GET returns 200 for webhook verification |
| UniSender Go | `202 Accepted` on success | GET returns 200 for webhook verification |
| GetStream | `202 Accepted` on success | GET returns 200 for webhook verification |
| Admin | `202 Accepted` on success | Static API key |

All POST endpoints return `503 Service Unavailable` when RabbitMQ does not confirm the publish
within `PUBLISH_TIMEOUT` (default 10s) or returns the message as unroutable — sources must retry.

---

### To Container Orchestration

| Endpoint | Purpose |
|---|---|
| `GET /health` | Liveness/readiness probe; returns `{"status": "ok"}` |

Source: `routes.py:84-87`

---

## What Breaks If This Service Goes Down

```mermaid
graph LR
    subgraph "Immediate Impact"
        A[Webhook sources get connection refused / timeout]
        B[No new events published to RabbitMQ]
    end

    subgraph "Cascading Impact"
        C[event-saver receives no new messages]
        D[No new bookings/events recorded in DB]
        E[Admin UI shows stale data]
        F[Webhook sources may retry, causing backlog]
    end

    A --> F
    B --> C
    C --> D
    D --> E
```

### Immediate Effects

1. **Webhook callers receive errors**: Booking service, Jitsi, UniSender Go, and GetStream webhook deliveries fail (connection refused or timeout)
2. **No events flow into the system**: RabbitMQ queues receive zero new messages
3. **Webhook retry storms**: External systems (UniSender Go, GetStream) will retry failed webhook deliveries, building a backlog that hits the service when it recovers

### Cascading Effects

4. **event-saver starves**: No new events to consume; DB state becomes stale
5. **event-admin shows stale data**: Read-only API reflects outdated state
6. **Booking lifecycle gaps**: Critical events (booking.created, booking.cancelled) are delayed until recovery
7. **User resolution calls stop**: event-users sees reduced traffic; events that do arrive will publish with `user_id=null` if event-users is also down

### Recovery Behavior

- On restart, the service re-declares RabbitMQ topology (idempotent)
- Webhook sources that retry will deliver missed events (eventual consistency)
- No local state to recover (stateless service)
- Events missed during downtime from sources that do NOT retry are permanently lost

### Single Points of Failure

| Failure | Effect | Mitigation |
|---|---|---|
| event-receiver down | All event ingestion stops | Run multiple replicas behind load balancer |
| RabbitMQ down | Publish fails, 500 to callers | RabbitMQ clustering; no local buffer in event-receiver |
| event-users down | Events published with `user_id=null` after 3 retry attempts | Retry with exponential backoff + graceful fallback to `user_id=None` |
| event-users slow | Webhook response latency increases (up to 3×10s in worst case per participant) | Consider async enrichment or timeout reduction |
