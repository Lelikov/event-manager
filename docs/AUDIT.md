# event-receiver Audit Findings

Audited: 2026-04-19

---

## CRITICAL

---

### ~~[CRITICAL] Booking lifecycle events routed to phantom queue `events.notifications`~~ — RESOLVED

Services affected: event-receiver, event-saver
Location: `event-receiver/event_receiver/config.py:9-34`
Description: The first 5 routing rules in `_default_route_rules()` send `booking.created`, `booking.cancelled`, `booking.rescheduled`, `booking.reassigned`, and `booking.reminder_sent` to a queue named `events.notifications`. Because `EventRouter.resolve_routing_key_by_fields()` returns the **first** matching rule, these rules shadow the correct `events.booking.lifecycle` and `events.booking.reminder` rules that appear later (lines 36-58). The queue `events.notifications` does not appear in `QUEUES_DIGEST.md`, is not declared by the topology manager (because it is not in `routing_destinations` either — it IS because `routing_destinations` includes all rule destinations), and is not consumed by `event-saver`. All booking lifecycle events are silently routed to an unconsumed queue; they will never reach event-saver. This is a data-loss bug.
**Resolution**: The five phantom `events.notifications` routing rules have been removed from `config.py`. Booking lifecycle events now correctly route to `events.booking.lifecycle` and `events.booking.reminder`.

---

### ~~[CRITICAL] JWT `verify()` raises `ValueError` instead of `UnauthorizedError` — uncaught 500 on claim mismatch~~ — RESOLVED

Services affected: event-receiver
Location: `event-receiver/event_receiver/security.py:81,88` + `event-receiver/event_receiver/routes.py:52-53`
Description: `AuthorizationJWTVerifier.verify()` raises `ValueError` when the JWT `source` or `type` claim does not match the incoming event (lines 81, 88 of security.py). The HTTP error mapper in `routes.py` only catches `IngestError` subclasses (`BadRequestError`, `UnauthorizedError`, `ConfigurationError`). A `ValueError` escapes this handler and is caught by the final `except IngestError` fallback — except `ValueError` is not an `IngestError`. FastAPI will therefore return a raw 500 with no log of the auth failure being a deliberate rejection. This is also a security issue because the caller receives no indication that their token was rejected for auth reasons.
**Resolution**: `security.py:verify()` now raises `UnauthorizedError` instead of `ValueError` on claim mismatch. Auth rejections are correctly returned as HTTP 401.

---

### ~~[CRITICAL] `RequestLoggerMiddleware` writes raw request bodies (including secrets) to an unrotated local file~~ — RESOLVED

Services affected: event-receiver
Location: `event-receiver/event_receiver/main.py:32,35-53`
Description: `RequestLoggerMiddleware` appends every POST request body (headers + decoded JSON) to `incoming_requests.jsonl` at a relative path (`Path("incoming_requests.jsonl")`). This file grows unboundedly with no rotation, size cap, or TTL. It is always active — there is no guard on `settings.debug`. In production, this will contain webhook payloads with auth tokens, emails, booking data, and API keys. The relative path resolves relative to the CWD of the uvicorn process, which may not be a writable or monitored location in a container. Additionally, the file is appended synchronously via `anyio.open_file` but without any concurrent-write protection — under load, multiple workers will race to append to the same file.
**Resolution**: `RequestLoggerMiddleware` is now gated behind `settings.debug`. It is not active in production (`DEBUG=False`).

---

### [CRITICAL] Module-level `container = make_async_container(...)` executes at import time before settings are validated

Services affected: event-receiver
Location: `event-receiver/event_receiver/main.py:56`
Description: `container = make_async_container(AppProvider(), FastapiProvider())` is executed unconditionally at module import time. `Settings()` construction (which reads and validates all required env vars with `Field(strict=True)`) will be called during the first DI resolution inside `lifespan`, but provider instantiation at import time means a broken settings object or missing env vars will not fail immediately on import. More critically, this also means that in any test or tooling that imports `main`, the full container (including Dishka's introspection) is constructed, and any import-time side effects in providers run immediately. The `container` is also a module-level global, making the application non-restartable within a single process.
Recommendation: Move container construction inside `lifespan` or into a factory function called from `lifespan`. Alternatively, accept this as a deliberate design choice but add a startup health-check that validates settings early and fails fast with a clear error message.

---

## HIGH

---

### ~~[HIGH] `ingest_getstream` raises `KeyError` on missing `X-SIGNATURE` header — no UnauthorizedError~~ — RESOLVED

Services affected: event-receiver
Location: `event-receiver/event_receiver/controllers/ingest.py:201`
Description: `headers["X-SIGNATURE"]` uses direct dict access on the Starlette `Headers` object. If the `X-SIGNATURE` header is absent (malformed or non-GetStream request hitting the endpoint), this raises a `KeyError`, which is not caught in the controller and is not an `IngestError`. The `routes.py` error mapper will return HTTP 500 instead of 401. This also logs at the wrong level — it will appear as an unexpected server error rather than an auth failure.
**Resolution**: `headers.get("X-SIGNATURE")` is now used; an explicit `None` check raises `UnauthorizedError("Missing X-SIGNATURE header")` before calling `client.verify_webhook`.

---

### ~~[HIGH] No retry or circuit-breaker on `event-users` HTTP calls inside publish path~~ — RESOLVED

Services affected: event-receiver, event-users
Location: `event-receiver/event_receiver/adapters/users_client.py:17-48` + `event-receiver/event_receiver/adapters/publisher.py:86-90`
Description: `UserResolver.resolve_or_create()` is called inside `CloudEventPublisher.publish()` for every participant in every event. There is no retry policy, exponential backoff, or circuit-breaker. A transient event-users timeout or HTTP 5xx will propagate as an unhandled `httpx.HTTPStatusError` or `httpx.TimeoutException` from the publish path, causing the HTTP response to the original webhook caller to be a 500. No message is published to RabbitMQ. The 10-second timeout (`ioc.py:93`) is correct but a single slow `event-users` call directly extends the webhook response latency by up to 10 seconds. `tenacity` is already in `pyproject.toml` dependencies but unused.
**Resolution**: `_get_user` and `_create_user` are now wrapped with `tenacity.retry` (exponential backoff, 3 attempts). On exhausted retries, `UserResolver` falls back gracefully to `user_id=None` rather than propagating the error to the caller. Event is still published to RabbitMQ with `user_id=None` in the participant entry.

---

### [HIGH] No idempotency enforcement — duplicate webhook delivery produces duplicate RabbitMQ messages

Services affected: event-receiver, event-saver
Location: `event-receiver/event_receiver/adapters/publisher.py:67-71`
Description: An idempotency key is generated (`generate_idempotency_key`) and included as a CloudEvent extension attribute, but there is no deduplication check at the event-receiver layer. If a webhook source retries (e.g., booking service POST fails to receive 202 due to network timeout), the same event is published to RabbitMQ again with the same idempotency key. Event-saver does perform deduplication via `ON CONFLICT DO NOTHING` on `(booking_id, event_type, source, md5(payload))`, but the idempotency key from event-receiver is not part of that constraint — meaning small differences in timestamp or ordering could bypass dedup. Additionally, the idempotency key is never stored or checked by event-receiver itself.
Recommendation: Document clearly (in CLAUDE.md and QUEUES_DIGEST.md) that idempotency is enforced by event-saver, not event-receiver, and confirm the event-saver dedup constraint covers all duplicate-entry scenarios. If event-receiver dedup is required, add a short-lived in-memory or Redis store keyed on idempotency_key with TTL of ~5 minutes.

---

### [HIGH] RabbitMQ broker connect failure at startup raises unhandled exception — no retry

Services affected: event-receiver
Location: `event-receiver/event_receiver/main.py:73-74`
Description: `await broker.connect()` in `lifespan` is called with no retry, timeout, or error handling. If RabbitMQ is not yet available (e.g., container startup race), the application will raise immediately, fail the lifespan, and FastAPI will return 503 on all subsequent health checks. Kubernetes liveness/readiness probes will terminate and restart the pod, causing a crash loop until RabbitMQ is ready. There is no startup retry window.
Recommendation: Wrap `broker.connect()` in a retry loop with exponential backoff and a maximum wait (e.g., using `tenacity.AsyncRetrying`). Log each retry attempt. Distinguish connection errors from configuration errors (wrong URL vs. broker unavailable).

---

### ~~[HIGH] `events.notifications` phantom queue will be declared and bound at startup~~ — RESOLVED

Services affected: event-receiver
Location: `event-receiver/event_receiver/adapters/publisher.py:161-196` + `event-receiver/event_receiver/config.py:10-30`
Description: Because `settings.routing_destinations` includes all rule destinations, `topology_queues` will include `events.notifications`. The topology manager will declare this queue, create a DLQ for it (`events.notifications.dlq`), and bind it to the exchange. No consumer will ever drain it. This is a waste of broker resources and will accumulate unacknowledged messages indefinitely. This is a consequence of the [CRITICAL] routing bug above but is separately observable.
**Resolution**: Resolved as a consequence of removing the phantom `events.notifications` routing rules (see CRITICAL finding above). The queue is no longer declared.

---

## MEDIUM

---

### [MEDIUM] `EVENTS_DIGEST.md` booking.created payload schema does not match actual ingestion code

Services affected: event-receiver
Location: `event-receiver/EVENTS_DIGEST.md:18-29` vs `event-receiver/event_receiver/controllers/ingest.py:148-160`
Description: `EVENTS_DIGEST.md` specifies `booking.created` payload as `user.email`, `user.time_zone`, `client.email`, `client.time_zone`, `start_time`, `end_time` (structured objects). But `_transform_booking_created_payload` in `ingest.py` expects a flat `users[]` list with `role` and `email` fields (line 151). The QUEUES_DIGEST.md payload section (line 119-130) also uses the structured format. The documentation is misaligned with the implementation. External callers reading the docs will send the wrong format.
Recommendation: Update `EVENTS_DIGEST.md` and `QUEUES_DIGEST.md` to reflect the actual accepted format: a `users` list with `{role: "organizer"|"client", email: str}` items. Alternatively, add a Pydantic model for the raw incoming payload and validate it before `_transform`.

---

### [MEDIUM] `PROJECT_CONTEXT.md` documents non-existent `/event/cloudevents` endpoint

Services affected: event-receiver
Location: `event-receiver/PROJECT_CONTEXT.md:33-42` + `event-receiver/CLAUDE.md:98-99`
Description: Both `PROJECT_CONTEXT.md` and `CLAUDE.md` document a `POST /event/cloudevents` endpoint with JWT authorization as a first-class route. This endpoint does not exist in `routes.py` or `INGEST_ROUTE_TO_METHOD`. The `IIngestController` protocol also has no `ingest_cloudevents` method. The service advertises a capability it does not provide. If there was a `/event/cloudevents` endpoint, it may have been removed without updating docs, or it may be a planned but unimplemented feature.
Recommendation: Either implement the `/event/cloudevents` endpoint (add `ingest_cloudevents` to `IIngestController`, implement in `IngestController`, register in `routes.py`), or remove all references to it from docs. The Jitsi endpoint currently re-uses the same JWT verifier, suggesting the CloudEvents endpoint may have been merged into Jitsi by mistake.

---

### [MEDIUM] `QUEUES_DIGEST.md` source_pattern column is incorrect — shows `*` instead of `booking`

Services affected: event-receiver
Location: `event-receiver/QUEUES_DIGEST.md:9-16` vs `event-receiver/event_receiver/config.py:36-58`
Description: The summary table in `QUEUES_DIGEST.md` lists `*` as the source pattern for `events.booking.lifecycle`, `events.booking.reminder`, `events.chat.lifecycle`, `events.chat.activity`, and `events.meeting.lifecycle`. In `config.py`, these rules use `source_pattern="booking"` (not `*`). This means any event of type `booking.created` from a non-`booking` source would not match in `config.py` but would per the docs. The docs are wrong, which could mislead anyone adding routing rules.
Recommendation: Update QUEUES_DIGEST.md table to show the actual `source_pattern="booking"` for booking lifecycle queues. Then decide whether this restriction is intentional and add a comment in `config.py` explaining why source is locked to `booking`.

---

### ~~[MEDIUM] CORS wildcard `allow_origins=["*"]` with `allow_credentials=True` is security misconfiguration~~ — RESOLVED

Services affected: event-receiver
Location: `event-receiver/event_receiver/main.py:95-99`
Description: `CORSMiddleware` is configured with `allow_origins=["*"]` and `allow_credentials=True`. Browsers will reject credentialed CORS responses when origin is a wildcard — this combination is invalid per the Fetch spec and will cause CORS failures in any browser-based client. More critically, it signals that CORS policy has not been thought through. The event-receiver is a webhook ingress service; it is unclear why CORS middleware is needed at all unless the frontend calls it directly.
**Resolution**: `allow_origins` is now read from the `CORS_ORIGINS` environment variable instead of being hardcoded as `["*"]`.

---

### ~~[MEDIUM] `ingest_jitsi` calls `verify_signature` then `verify` with the same token — double-decode and security gap~~ — RESOLVED

Services affected: event-receiver
Location: `event-receiver/event_receiver/controllers/ingest.py:47,64-68`
Description: `ingest_jitsi` calls `verify_signature(token=headers.get("Authorization"))` first to validate the JWT signature, then calls `verify(token=..., event_source=..., event_type=...)` which decodes the JWT a second time with `verify_signature=False`. The second call skips all cryptographic verification — it only checks claims without re-verifying the signature. This means the second `jwt.decode` call trusts any token that was initially valid, but if there is a TOCTOU bug (e.g., the header value changes between calls, which cannot happen here but is architecturally fragile), it would silently trust an unverified token. Also, passing `token=headers.get("Authorization")` (which can be `None`) to `verify_signature` does not raise a typed domain error — PyJWT will raise `jwt.DecodeError` which bubbles as a non-`IngestError`.
**Resolution**: `verify()` now accepts pre-parsed claims returned by `verify_signature()` instead of re-decoding the token. The double-decode is eliminated and signature verification cannot be bypassed.

---

### ~~[MEDIUM] `ingest_booking` mutates `incoming.data` in place via `.pop()` — unexpected side effects~~ — RESOLVED

Services affected: event-receiver
Location: `event-receiver/event_receiver/controllers/ingest.py:111`
Description: `booking_uid = incoming.data.pop("booking_uid", None)` mutates the CloudEvent's data dict in place. If `incoming.data` is referenced elsewhere (e.g., by `from_http` internals, or in future code that logs the original event), the `booking_uid` will be missing. This is also inconsistent with `ingest_unisender_go` line 186, which also `.pop()`s from nested dict. Mutation of parsed input objects is error-prone and violates the expectation that parsed data is read-only.
**Resolution**: A shallow dict copy (`data = dict(incoming.data)`) is now used before calling `.pop()`, preserving the original CloudEvent data.

---

### [MEDIUM] `pyproject.toml` pins event-schemas via local absolute path — breaks in CI/CD and other environments

Services affected: event-receiver
Location: `event-receiver/pyproject.toml:11`
Description: `"event-schemas @ file:///Users/alexandrlelikov/PycharmProjects/events/event-schemas"` uses a developer's absolute local path. This will fail in CI, Docker builds (unless the file is explicitly copied into the build context), or any other developer's machine. The `git+https://github.com/...` alternative is commented out on line 12.
Recommendation: In Docker builds, use a multi-stage build that copies `event-schemas` before installing `event-receiver`, or publish `event-schemas` to a private PyPI/artifact registry. Switch the active dependency to the `git+https` form for non-local environments. Add a comment explaining the development vs. deployment path.

---

### ~~[MEDIUM] `ingest_unisender_go` has no event_id or event_time — publishes CloudEvents with missing required attributes~~ — RESOLVED

Services affected: event-receiver, event-saver
Location: `event-receiver/event_receiver/controllers/ingest.py:185-193`
Description: When publishing UniSender events, `self._publisher.publish()` is called without `event_id` or `event_time` arguments. In `publisher.py`, these become `None` and are omitted from the CloudEvent attributes (lines 105-108). The CloudEvents spec requires `id` and `time` attributes in all events. event-saver's consumer (`consumer.py:98`) calls `event["time"]` — if `time` is absent, this raises a `KeyError` and the message will be nacked/dead-lettered.
**Resolution**: `ingest_unisender_go` now generates `event_id = str(uuid.uuid4())` and `event_time = datetime.now(UTC).isoformat()` before calling `publish()`.

---

### [MEDIUM] `normalizers.py` silently returns empty participants on `ValidationError`, `KeyError`, `ValueError`

Services affected: event-receiver, event-saver
Location: `event-receiver/event_receiver/normalizers.py:45-49`
Description: `normalize_event_payload` catches `ValidationError`, `KeyError`, and `ValueError` silently and returns `{"normalized": {"participants": []}}`. A schema mismatch (e.g., malformed `booking.created` payload) will not surface as an error — the event will be published to RabbitMQ with no participants. event-saver will save the event but participant enrichment will be missing. This makes schema regressions invisible at runtime.
Recommendation: At minimum, log the exception at `warning` level with event_type and a repr of the error before swallowing it. Consider whether participant extraction failure should be a hard error for CRITICAL event types (e.g., `booking.created`) vs. a soft fallback for external events (UniSender, GetStream).

---

### [MEDIUM] `RABBITMQ_MESSAGES_SPEC.md` exists but was not referenced or verified

Services affected: event-receiver
Location: `event-receiver/RABBITMQ_MESSAGES_SPEC.md`
Description: A `RABBITMQ_MESSAGES_SPEC.md` file exists at the service root but is not referenced in any other documentation or in `CLAUDE.md`. Its contents were not cross-verified against the actual publisher code during this audit.
Recommendation: Verify this file's accuracy against `adapters/publisher.py` and `config.py`. Either incorporate it into `QUEUES_DIGEST.md` or explicitly reference it from `CLAUDE.md`.

---

## LOW

---

### [LOW] `logger.py` suppresses logging for `aiokafka`, `asyncio_redis`, and `botocore` — irrelevant packages

Services affected: event-receiver
Location: `event-receiver/event_receiver/logger.py:67-71`
Description: `configure_default_logging` sets ERROR-level logging for `aiokafka`, `asyncio_redis`, and `botocore`. These packages are not used by event-receiver (it uses RabbitMQ, not Kafka/Redis/AWS). This is dead configuration copied from another service and suggests the logger setup was not reviewed.
Recommendation: Remove `aiokafka`, `asyncio_redis`, and `botocore` log suppression. Add `aio_pika` and `faststream` suppression instead if their default log verbosity is excessive.

---

### [LOW] `schemas.py` is an empty placeholder with no planned content

Services affected: event-receiver
Location: `event-receiver/event_receiver/schemas.py`
Description: The file contains only a docstring `"""Schemas module kept for future API DTOs."""` and is imported by nothing. This creates noise in the module structure without purpose.
Recommendation: Remove `schemas.py` until it is needed, or replace with actual request/response models for the ingest endpoints.

---

### [LOW] CLAUDE.md service name is `event-manager` but the package and Docker image are `event-receiver`

Services affected: event-receiver
Location: `event-receiver/CLAUDE.md:3`, `event-receiver/project_context.md:1`
Description: Both docs call the service `event-manager`, while the Python package is `event_receiver`, the docker image is named `event-receiver`, and the FastAPI app title is `event-manager` (main.py:89). The inconsistency makes searching and referencing the service ambiguous.
Recommendation: Standardize on `event-receiver` as the canonical name. Update `CLAUDE.md`, `PROJECT_CONTEXT.md`, and `FastAPI(title=...)` to use the same name.

---

### [LOW] `ioc.py` provides `Callable` type annotation (bare) for getstream decoder

Services affected: event-receiver
Location: `event-receiver/event_receiver/ioc.py:80,108`
Description: `provide_getstream_decoder` returns `Callable` (bare, without type parameters) and is typed as `Callable` in `provide_publisher`. Using bare `Callable` prevents type checkers from verifying that the decoder function signature matches `Callable[[str], str]` expected in `CloudEventPublisher.__init__`. Ruff config ignores `ANN401` (Any annotation), masking this.
Recommendation: Replace `-> Callable` with `-> Callable[[str], str]` in `ioc.py:80` and `provide_publisher` parameter. Also fix `publisher.py:29`: `getstream_decoder: callable[[str], str]` uses lowercase `callable` (which is a built-in function, not a type) — change to `Callable[[str], str]`.

---

### ~~[LOW] `IngestController` is in `Scope.REQUEST` but has no per-request state — unnecessary allocation~~ — RESOLVED

Services affected: event-receiver
Location: `event-receiver/event_receiver/ioc.py:133-145`
Description: `IngestController` is provided at `Scope.REQUEST`, creating a new instance on every HTTP request. Examining `IngestController.__init__`, all its fields (`_settings`, `_publisher`, `_authorization_jwt_verifier`) are `Scope.APP` singletons. There is no request-scoped state in the controller. Creating it per-request adds overhead without benefit.
**Resolution**: `IngestController` is now provided at `Scope.APP`.

---

### [LOW] No automated tests exist for the service

Services affected: event-receiver
Location: `event-receiver/` (project root — no `tests/` directory outside `.venv`)
Description: No `tests/` directory was found at the service level. The `pyproject.toml` has no `pytest` or `httpx[client]` test dependency. The `CLAUDE.md` references a test via ruff's `tests/*.py` file ignore rules, implying tests were planned but never written. There is no coverage for: routing logic, all 4 auth methods, normalizer edge cases, or publish path.
Recommendation: Add at minimum: (1) unit tests for `EventRouter.resolve_routing_key_by_fields` with the full rule set; (2) unit tests for each auth method's happy/unhappy paths; (3) unit tests for `normalize_event_payload` with each event type. Use `pytest-asyncio` for async tests. The routing bug found in this audit (CRITICAL #1) would have been caught by a single routing test.

---

## Summary

| Severity | Open | Resolved | Total |
|---|---|---|---|
| CRITICAL | 1 | 3 | 4 |
| HIGH | 3 | 2 | 5 |
| MEDIUM | 5 | 4 | 9 |
| LOW | 5 | 1 | 6 |
| **Total** | **14** | **10** | **24** |

### Top Open Concerns

1. **Module-level container construction executes at import time** (`CRITICAL` — main.py). `make_async_container(...)` is called unconditionally at module import time, causing container and provider instantiation before settings are validated.

2. **No idempotency enforcement at event-receiver layer** (`HIGH` — publisher.py). Duplicate webhook deliveries produce duplicate RabbitMQ messages; deduplication is delegated entirely to event-saver's DB constraints.

3. **RabbitMQ connect failure at startup has no retry** (`HIGH` — main.py). `broker.connect()` in `lifespan` is called with no retry or timeout, causing crash-loop restarts if RabbitMQ is not yet ready.
