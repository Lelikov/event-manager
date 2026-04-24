# event-receiver Full Refactoring Design

**Date:** 2026-04-21
**Scope:** 24 findings (4 CRITICAL, 5 HIGH, 9 MEDIUM, 6 LOW) across the event-receiver service
**Approach:** By domain of responsibility — 6 phases, each a logically isolated group of changes

---

## Phase 1: Routing & Topology

**Goal:** Fix data-loss bug where booking events never reach event-saver.

**Changes:**
- `config.py`: Remove first 5 routing rules (lines 9-34) that route booking events to phantom queue `events.notifications`, shadowing correct rules for `events.booking.lifecycle` / `events.booking.reminder`
- Remove `events.notifications` from topology queues if declared explicitly
- Verify remaining rule order: specific patterns first, fallback last

**Addresses:** C-1 (routing shadows booking.lifecycle), H-8 (phantom queue declared)

---

## Phase 2: Security & Auth

**Goal:** Fix auth failures returning 500 instead of 401, remove secret-leaking middleware, fix CORS.

**Changes:**
- `security.py`: Replace `raise ValueError(...)` with `raise UnauthorizedError(...)` in JWT claim verification (2 locations)
- `controllers/ingest.py`: Replace `headers["X-SIGNATURE"]` with `headers.get("X-SIGNATURE")` + None check raising `UnauthorizedError`
- `main.py`: Remove `RequestLoggerMiddleware` entirely — structlog already covers request logging
- `main.py`: Remove `allow_credentials=True` from CORS config (webhook ingress doesn't need browser credentials)

**Addresses:** C-2 (JWT ValueError), C-4 (RequestLoggerMiddleware secrets), H-4 (KeyError on missing header), M-4 (CORS misconfiguration)

---

## Phase 3: Publishing Pipeline

**Goal:** Add resilience to event-users calls, fix CloudEvents spec compliance, make normalizer failures visible.

**Changes:**
- `adapters/users_client.py`: Add tenacity retry — 3 attempts, exponential backoff, retry on `httpx.TimeoutException` and 5xx responses
- `controllers/ingest.py`: Generate `event_id=str(uuid4())` and `event_time=datetime.now(UTC)` in `ingest_unisender_go` before publish
- `normalizers.py`: Replace silent exception catch with `logger.warning()` including event_type and error context
- `security.py` + `controllers/ingest.py`: Refactor Jitsi JWT flow — `verify_signature()` returns decoded claims, pass to `verify()` without re-decoding

**Addresses:** H-2 (no retry on event-users), M-8 (missing event_id/event_time), M-9 (silent exception swallowing), M-5 (double JWT decode)

---

## Phase 4: Infrastructure Cleanup

**Goal:** Fix container lifecycle, DI scopes, type annotations, dead code.

**Changes:**
- `main.py`: Move `container = make_async_container(...)` into `lifespan()`, pass via `app.state`
- `ioc.py`: Move `IngestController` from `Scope.REQUEST` to `Scope.APP`
- Fix type annotations: `callable[[str], str]` -> `Callable[[str], str]` (from `collections.abc`)
- `logger.py`: Remove suppression for unused packages (aiokafka, asyncio_redis, botocore)
- Delete empty `schemas.py`
- Replace `event-manager` naming with `event-receiver` where found

**Addresses:** C-12 (import-time container), L-5 (unnecessary REQUEST scope), L-4 (bare Callable), L-1 (dead logger config), L-2 (empty schemas.py), L-3 (naming inconsistency)

---

## Phase 5: Documentation

**Goal:** Reconcile documentation with actual code behavior.

**Changes:**
- `EVENTS_DIGEST.md`: Update payload schemas to match actual `users[]` list format (not `user`/`client` objects)
- `QUEUES_DIGEST.md`: Fix source_pattern to `booking` (not `*`), remove `events.notifications`, align with corrected routing rules
- `PROJECT_CONTEXT.md`: Remove `/event/cloudevents` endpoint, update endpoint list to match routes.py

**Addresses:** M-1 (EVENTS_DIGEST schema mismatch), M-3 (QUEUES_DIGEST wrong patterns), M-2 (non-existent endpoint documented)

---

## Phase 6: Tests

**Goal:** Add unit test coverage for critical modules.

**Changes:**
- Add `pytest` + `pytest-asyncio` to dev dependencies in `pyproject.toml`
- Add pytest config section to `pyproject.toml`
- `tests/test_routing.py`: EventRouter first-match, glob patterns, fallback, rule ordering
- `tests/test_security.py`: JWT valid token, expired token, claim mismatch -> UnauthorizedError, missing signature -> UnauthorizedError
- `tests/test_normalizers.py`: Participant extraction for booking/chat events, malformed payload -> warning + empty list

**Addresses:** L-6 (no automated tests)

---

## Out of Scope

- Idempotency enforcement at event-receiver level (H-6) — by design delegated to event-saver
- Rate limiting / DDoS protection — infrastructure concern (API gateway)
- pyproject.toml absolute local path for event-schemas (M-7) — CI/Docker concern, separate task
