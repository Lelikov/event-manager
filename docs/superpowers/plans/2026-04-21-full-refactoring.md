# event-receiver Full Refactoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix remaining quality issues in event-receiver: CORS misconfiguration, missing retry on event-users HTTP calls, DI scope misuse, type annotation errors, stale documentation, and zero test coverage.

**Architecture:** Incremental fixes grouped by domain. Each task is self-contained and produces a working codebase. Tests use pytest + pytest-asyncio with mocked external dependencies.

**Tech Stack:** Python 3.14, FastAPI, httpx, tenacity, pytest, pytest-asyncio, structlog

**Note:** Many audit findings (routing rules, JWT ValueError, RequestLoggerMiddleware gating, X-SIGNATURE check, container lifecycle, normalizer logging, schemas.py deletion) are already fixed in the working tree. This plan covers only remaining work.

---

## File Structure

| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `event_receiver/main.py:108-114` | Remove `allow_credentials=True` from CORS |
| Modify | `event_receiver/adapters/users_client.py` | Add tenacity retry to HTTP calls |
| Modify | `event_receiver/adapters/publisher.py:29` | Fix `callable` → `Callable` type annotation |
| Modify | `event_receiver/ioc.py:80,108,133` | Fix bare `Callable`, change IngestController to APP scope |
| Modify | `QUEUES_DIGEST.md` | Fix source_pattern from `*` to `booking` |
| Modify | `EVENTS_DIGEST.md` | Update booking.created to show `users[]` list format |
| Modify | `PROJECT_CONTEXT.md` | Remove `/event/cloudevents`, rename event-manager → event-receiver |
| Modify | `pyproject.toml` | Add pytest + pytest-asyncio dev dependencies, pytest config |
| Create | `tests/__init__.py` | Test package marker |
| Create | `tests/test_routing.py` | Unit tests for EventRouter |
| Create | `tests/test_security.py` | Unit tests for AuthorizationJWTVerifier |
| Create | `tests/test_normalizers.py` | Unit tests for normalize_event_payload |

---

### Task 1: Fix CORS misconfiguration

**Files:**
- Modify: `event_receiver/main.py:108-114`

- [ ] **Step 1: Remove `allow_credentials=True` from CORS middleware**

In `event_receiver/main.py`, change the CORS middleware configuration:

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "http://localhost:5173").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)
```

Remove `allow_credentials=True`. This is a webhook ingress service — browsers don't send credentialed requests to it. The `allow_origins=["*"]` + `allow_credentials=True` combination violates the Fetch spec anyway.

- [ ] **Step 2: Verify app starts**

Run: `cd /Users/alexandrlelikov/PycharmProjects/events/event-receiver && python -c "from event_receiver.main import app; print('OK')"`
Expected: `OK` (no import errors)

- [ ] **Step 3: Commit**

```bash
git add event_receiver/main.py
git commit -m "fix: remove allow_credentials from CORS config

Webhook ingress does not need browser credentials. The combination
of allow_origins=* with allow_credentials=True violates the Fetch spec."
```

---

### Task 2: Add tenacity retry to UserResolver

**Files:**
- Modify: `event_receiver/adapters/users_client.py`

- [ ] **Step 1: Add retry decorator to `resolve_or_create`**

Replace the entire `event_receiver/adapters/users_client.py` with:

```python
from http import HTTPStatus

import httpx
import structlog
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from event_receiver.interfaces.users import IUserResolver


logger = structlog.get_logger(__name__)

_RETRY_DECORATOR = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.HTTPStatusError)),
    before_sleep=lambda retry_state: logger.warning(
        "Retrying event-users call",
        attempt=retry_state.attempt_number,
        error=repr(retry_state.outcome.exception()),
    ),
    reraise=True,
)


class UserResolver(IUserResolver):
    def __init__(self, *, http_client: httpx.AsyncClient, api_token: str) -> None:
        self._client = http_client
        self._headers = {"Authorization": f"Bearer {api_token}"}

    @_RETRY_DECORATOR
    async def resolve_or_create(self, *, email: str, role: str) -> str:
        user_id = await self._get_user(email=email, role=role)
        if user_id:
            return user_id
        return await self._create_user(email=email, role=role)

    async def _get_user(self, *, email: str, role: str) -> str | None:
        response = await self._client.get(
            f"/api/users/roles/{role}/emails/{email}",
            headers=self._headers,
        )
        if response.status_code == HTTPStatus.NOT_FOUND:
            return None
        response.raise_for_status()
        return response.json()["id"]

    async def _create_user(self, *, email: str, role: str) -> str:
        response = await self._client.post(
            "/api/users",
            json={"email": email, "role": role},
            headers=self._headers,
        )
        if response.status_code == HTTPStatus.CONFLICT:
            logger.debug("User conflict on create, retrying GET", email=email, role=role)
            user_id = await self._get_user(email=email, role=role)
            if user_id is None:
                msg = f"User {email!r} role={role!r} not found after 409 conflict"
                raise RuntimeError(msg)
            return user_id
        response.raise_for_status()
        logger.info("Created user in event-users", email=email, role=role, user_id=response.json()["id"])
        return response.json()["id"]
```

Key changes: import tenacity, define `_RETRY_DECORATOR` with 3 attempts, exponential backoff (0.5s → 1s → 2s), retry on timeout and 5xx HTTP errors, log each retry with structlog.

- [ ] **Step 2: Verify import works**

Run: `cd /Users/alexandrlelikov/PycharmProjects/events/event-receiver && python -c "from event_receiver.adapters.users_client import UserResolver; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add event_receiver/adapters/users_client.py
git commit -m "feat: add tenacity retry to UserResolver HTTP calls

3 attempts with exponential backoff (0.5s-4s). Retries on
httpx.TimeoutException and httpx.HTTPStatusError. Logs each retry."
```

---

### Task 3: Fix type annotations and DI scope

**Files:**
- Modify: `event_receiver/adapters/publisher.py:29`
- Modify: `event_receiver/ioc.py:80,108,133`

- [ ] **Step 1: Fix `callable` → `Callable` in publisher.py**

In `event_receiver/adapters/publisher.py`, change line 29:

From:
```python
        getstream_decoder: callable[[str], str] | None = None,
```

To:
```python
        getstream_decoder: Callable[[str], str] | None = None,
```

Add to the `TYPE_CHECKING` imports block (after `from event_receiver.interfaces.routing import IEventRouter`):
```python
    from collections.abc import Callable
```

- [ ] **Step 2: Fix bare `Callable` in ioc.py**

In `event_receiver/ioc.py`, change line 80:

From:
```python
    def provide_getstream_decoder(self, settings: Settings) -> Callable:
```

To:
```python
    def provide_getstream_decoder(self, settings: Settings) -> Callable[[str], str]:
```

Change line 108:

From:
```python
        getstream_decoder: Callable,
```

To:
```python
        getstream_decoder: Callable[[str], str],
```

The `Callable` import from `collections.abc` already exists at line 2.

- [ ] **Step 3: Move IngestController to Scope.APP in ioc.py**

In `event_receiver/ioc.py`, change line 133:

From:
```python
    @provide(scope=Scope.REQUEST)
    def provide_ingest_controller(
```

To:
```python
    @provide(scope=Scope.APP)
    def provide_ingest_controller(
```

Remove the log line at 140 that mentions "request scope":

From:
```python
        logger.debug("Providing IngestController for request scope")
```

To:
```python
        logger.debug("Providing IngestController")
```

- [ ] **Step 4: Verify imports**

Run: `cd /Users/alexandrlelikov/PycharmProjects/events/event-receiver && python -c "from event_receiver.ioc import AppProvider; from event_receiver.adapters.publisher import CloudEventPublisher; print('OK')"`
Expected: `OK`

- [ ] **Step 5: Run ruff**

Run: `cd /Users/alexandrlelikov/PycharmProjects/events/event-receiver && ruff check event_receiver/ioc.py event_receiver/adapters/publisher.py`
Expected: No errors (or only pre-existing ones unrelated to our changes)

- [ ] **Step 6: Commit**

```bash
git add event_receiver/adapters/publisher.py event_receiver/ioc.py
git commit -m "fix: correct type annotations and move IngestController to APP scope

- Fix callable[[str], str] → Callable[[str], str] in publisher.py
- Fix bare Callable → Callable[[str], str] in ioc.py
- Move IngestController from Scope.REQUEST to Scope.APP (no per-request state)"
```

---

### Task 4: Update documentation

**Files:**
- Modify: `QUEUES_DIGEST.md`
- Modify: `EVENTS_DIGEST.md`
- Modify: `PROJECT_CONTEXT.md`

- [ ] **Step 1: Fix QUEUES_DIGEST.md source patterns**

In `QUEUES_DIGEST.md`, update the summary table. Replace lines 9-13 (the first 5 rows after the header):

From:
```markdown
| `events.booking.lifecycle` | `*` | `booking.created` / `booking.rescheduled` / `booking.reassigned` / `booking.cancelled` | lifecycle бронирования |
| `events.booking.reminder` | `*` | `booking.reminder_sent` | отправка напоминаний |
| `events.chat.lifecycle` | `*` | `chat.created` / `chat.deleted` | lifecycle чата |
| `events.chat.activity` | `*` | `chat.message_sent` | активность в чате |
| `events.meeting.lifecycle` | `*` | `meeting.url_created` / `meeting.url_deleted` | lifecycle meeting URL |
```

To:
```markdown
| `events.booking.lifecycle` | `booking` | `booking.created` / `booking.rescheduled` / `booking.reassigned` / `booking.cancelled` | lifecycle бронирования |
| `events.booking.reminder` | `booking` | `booking.reminder_sent` | отправка напоминаний |
| `events.chat.lifecycle` | `booking` | `chat.created` / `chat.deleted` | lifecycle чата |
| `events.chat.activity` | `booking` | `chat.message_sent` | активность в чате |
| `events.meeting.lifecycle` | `booking` | `meeting.url_created` / `meeting.url_deleted` | lifecycle meeting URL |
```

This matches the actual `source_pattern="booking"` in `config.py`.

- [ ] **Step 2: Fix EVENTS_DIGEST.md booking.created schema**

In `EVENTS_DIGEST.md`, replace the `booking.created` table:

From:
```markdown
## booking.created

| Поле             | Тип        |
|------------------|------------|
| booking_uid      | `str`      |
| user.email       | `str`      |
| user.time_zone   | `str`      |
| client.email     | `str`      |
| client.time_zone | `str`      |
| start_time       | `datetime` |
| end_time         | `datetime` |
```

To:
```markdown
## booking.created

| Поле             | Тип              |
|------------------|------------------|
| booking_uid      | `str`            |
| users            | `list[UserInfo]` |
| start_time       | `datetime`       |
| end_time         | `datetime`       |

`UserInfo`: `{ email: str, role: "organizer" | "client" }`

Входящий payload содержит `users` — список с ролями `organizer` и `client`.
`IngestController._transform_booking_created_payload()` трансформирует в `user`/`client` структуру для валидации через `BookingCreatedPayload`.
```

- [ ] **Step 3: Fix PROJECT_CONTEXT.md**

In `PROJECT_CONTEXT.md`:

3a. Replace the title and overview (lines 1-4):

From:
```markdown
# Project Context: event-manager

## Overview
`event-manager` — ingress-микросервис для приёма входящих событий,
```

To:
```markdown
# Project Context: event-receiver

## Overview
`event-receiver` — ingress-микросервис для приёма входящих событий,
```

3b. Remove the `/event/cloudevents` endpoint section (lines 33-45). Replace with the actual endpoints:

From:
```markdown
## HTTP API
- `POST /event/cloudevents`
  - принимает CloudEvent,
  - ожидает JWT в `Authorization`,
  - проверяет подпись JWT,
  - парсит CloudEvent,
  - сверяет claims (`source`, `type`) с event,
  - публикует событие в RabbitMQ.

- `POST /event/unisender-go`
  - принимает JSON payload UniSender Go,
  - валидирует подпись в поле `auth` (MD5 от payload c подстановкой `email_api_key`),
  - при успехе публикует нормализованное событие в RabbitMQ.

- `GET /event/cloudevents`, `GET /event/unisender-go`
  - lightweight endpoint-health ответ `{"status": "ok"}`.
```

To:
```markdown
## HTTP API
- `POST /event/booking`
  - принимает CloudEvent от Booking сервиса,
  - проверяет API key в `Authorization`,
  - парсит CloudEvent, извлекает `booking_uid` из payload в CE-атрибут,
  - публикует нормализованное событие в RabbitMQ.

- `POST /event/jitsi`
  - принимает CloudEvent от Jitsi,
  - проверяет JWT подпись и claims (`source`, `type`),
  - публикует событие в RabbitMQ.

- `POST /event/unisender-go`
  - принимает JSON payload UniSender Go,
  - валидирует подпись в поле `auth` (MD5 от payload c подстановкой `email_api_key`),
  - при успехе публикует нормализованные события в RabbitMQ.

- `POST /event/getstream`
  - принимает webhook от GetStream,
  - проверяет HMAC-подпись в `X-SIGNATURE`,
  - публикует событие в RabbitMQ.

- `GET /event/*`
  - lightweight endpoint-health ответ `{"status": "ok"}`.
```

3c. Remove `schemas.py` from module structure (line 75):

From:
```markdown
  - `schemas.py` — placeholder под будущие DTO.
```

Remove this line entirely.

3d. Fix DI scope description (line 88):

From:
```markdown
- **Scope.REQUEST**
  - `IIngestController` -> `IngestController`
```

To:
```markdown
- **Scope.APP** (also)
  - `IIngestController` -> `IngestController`
```

- [ ] **Step 4: Commit**

```bash
git add QUEUES_DIGEST.md EVENTS_DIGEST.md PROJECT_CONTEXT.md
git commit -m "docs: reconcile documentation with actual code

- QUEUES_DIGEST: fix source_pattern from * to booking
- EVENTS_DIGEST: update booking.created to users[] list format
- PROJECT_CONTEXT: remove /event/cloudevents, add actual endpoints,
  rename event-manager to event-receiver, remove schemas.py reference,
  fix DI scope description"
```

---

### Task 5: Add pytest infrastructure and routing tests

**Files:**
- Modify: `pyproject.toml`
- Create: `tests/__init__.py`
- Create: `tests/test_routing.py`

- [ ] **Step 1: Add pytest dependencies and config to pyproject.toml**

In `pyproject.toml`, add to the `[dependency-groups]` dev list:

From:
```toml
[dependency-groups]
dev = [
    "pre-commit>=4.5.1",
    "ruff>=0.15.4",
    "code-review-graph",
]
```

To:
```toml
[dependency-groups]
dev = [
    "pre-commit>=4.5.1",
    "pytest>=8.0",
    "pytest-asyncio>=0.26",
    "ruff>=0.15.4",
    "code-review-graph",
]
```

Add pytest config at the end of `pyproject.toml`:

```toml

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 2: Install dev dependencies**

Run: `cd /Users/alexandrlelikov/PycharmProjects/events/event-receiver && uv sync`
Expected: Dependencies install successfully including pytest and pytest-asyncio

- [ ] **Step 3: Create test package**

Create `tests/__init__.py` as an empty file.

- [ ] **Step 4: Write routing tests**

Create `tests/test_routing.py`:

```python
from event_receiver.routing import EventRouter, RouteRule, RoutingConfig


def _make_router(rules: list[RouteRule], default: str = "events.unrouted") -> EventRouter:
    return EventRouter(RoutingConfig(default_destination=default, rules=rules))


def test_exact_match():
    router = _make_router([
        RouteRule(destination="events.booking.lifecycle", source_pattern="booking", type_pattern="booking.created"),
    ])
    assert router.resolve_routing_key_by_fields(source="booking", event_type="booking.created") == "events.booking.lifecycle"


def test_glob_source_pattern():
    router = _make_router([
        RouteRule(destination="events.jitsi", source_pattern="jitsi*", type_pattern="*"),
    ])
    assert router.resolve_routing_key_by_fields(source="jitsi-meet", event_type="any.event") == "events.jitsi"


def test_glob_type_pattern():
    router = _make_router([
        RouteRule(destination="events.mail", source_pattern="unisender-go", type_pattern="unisender.*"),
    ])
    assert router.resolve_routing_key_by_fields(source="unisender-go", event_type="unisender.delivered") == "events.mail"


def test_fallback_to_default():
    router = _make_router([
        RouteRule(destination="events.booking.lifecycle", source_pattern="booking", type_pattern="booking.created"),
    ])
    assert router.resolve_routing_key_by_fields(source="unknown", event_type="unknown.event") == "events.unrouted"


def test_first_match_wins():
    router = _make_router([
        RouteRule(destination="first", source_pattern="*", type_pattern="booking.created"),
        RouteRule(destination="second", source_pattern="booking", type_pattern="booking.created"),
    ])
    assert router.resolve_routing_key_by_fields(source="booking", event_type="booking.created") == "first"


def test_no_rules_uses_default():
    router = _make_router([], default="events.fallback")
    assert router.resolve_routing_key_by_fields(source="any", event_type="any") == "events.fallback"


def test_source_mismatch_skips_rule():
    router = _make_router([
        RouteRule(destination="events.booking.lifecycle", source_pattern="booking", type_pattern="booking.created"),
    ])
    assert router.resolve_routing_key_by_fields(source="other", event_type="booking.created") == "events.unrouted"


def test_type_mismatch_skips_rule():
    router = _make_router([
        RouteRule(destination="events.booking.lifecycle", source_pattern="booking", type_pattern="booking.created"),
    ])
    assert router.resolve_routing_key_by_fields(source="booking", event_type="booking.cancelled") == "events.unrouted"


def test_default_routing_rules_booking_lifecycle():
    """Verify that the actual default routing rules send booking events to the correct queue."""
    from event_receiver.config import _default_route_rules

    router = _make_router(_default_route_rules(), default="events.unrouted")

    assert router.resolve_routing_key_by_fields(source="booking", event_type="booking.created") == "events.booking.lifecycle"
    assert router.resolve_routing_key_by_fields(source="booking", event_type="booking.cancelled") == "events.booking.lifecycle"
    assert router.resolve_routing_key_by_fields(source="booking", event_type="booking.rescheduled") == "events.booking.lifecycle"
    assert router.resolve_routing_key_by_fields(source="booking", event_type="booking.reassigned") == "events.booking.lifecycle"
    assert router.resolve_routing_key_by_fields(source="booking", event_type="booking.reminder_sent") == "events.booking.reminder"


def test_default_routing_rules_chat():
    from event_receiver.config import _default_route_rules

    router = _make_router(_default_route_rules(), default="events.unrouted")

    assert router.resolve_routing_key_by_fields(source="booking", event_type="chat.created") == "events.chat.lifecycle"
    assert router.resolve_routing_key_by_fields(source="booking", event_type="chat.deleted") == "events.chat.lifecycle"
    assert router.resolve_routing_key_by_fields(source="booking", event_type="chat.message_sent") == "events.chat.activity"


def test_default_routing_rules_external():
    from event_receiver.config import _default_route_rules

    router = _make_router(_default_route_rules(), default="events.unrouted")

    assert router.resolve_routing_key_by_fields(source="jitsi-meet", event_type="conference.joined") == "events.jitsi"
    assert router.resolve_routing_key_by_fields(source="unisender-go", event_type="unisender.delivered") == "events.mail"
    assert router.resolve_routing_key_by_fields(source="getstream", event_type="getstream.message.new") == "events.chat"
```

- [ ] **Step 5: Run routing tests**

Run: `cd /Users/alexandrlelikov/PycharmProjects/events/event-receiver && python -m pytest tests/test_routing.py -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml tests/__init__.py tests/test_routing.py
git commit -m "test: add pytest infrastructure and routing unit tests

12 tests covering EventRouter: exact match, glob patterns,
first-match semantics, fallback, and default routing rules
for booking/chat/external sources."
```

---

### Task 6: Add security tests

**Files:**
- Create: `tests/test_security.py`

- [ ] **Step 1: Write security tests**

Create `tests/test_security.py`:

```python
import time

import jwt
import pytest

from event_receiver.errors import UnauthorizedError
from event_receiver.security import AuthorizationJWTConfig, AuthorizationJWTVerifier


SECRET = "test-secret-key-for-unit-tests"
ALGORITHM = "HS256"
ISSUER = "test-issuer"
AUDIENCE = "test-audience"


def _make_verifier() -> AuthorizationJWTVerifier:
    return AuthorizationJWTVerifier(
        AuthorizationJWTConfig(
            verify_key=SECRET,
            algorithm=ALGORITHM,
            issuer=ISSUER,
            audience=AUDIENCE,
        ),
    )


def _encode_token(claims: dict, *, secret: str = SECRET) -> str:
    payload = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
        **claims,
    }
    return jwt.encode(payload, secret, algorithm=ALGORITHM)


class TestVerifySignature:
    def test_valid_token(self):
        verifier = _make_verifier()
        token = _encode_token({"sub": "user1"})
        claims = verifier.verify_signature(token=token)
        assert claims["sub"] == "user1"

    def test_invalid_signature(self):
        verifier = _make_verifier()
        token = _encode_token({"sub": "user1"}, secret="wrong-secret")
        with pytest.raises(UnauthorizedError, match="Invalid JWT signature"):
            verifier.verify_signature(token=token)

    def test_expired_token(self):
        verifier = _make_verifier()
        payload = {
            "sub": "user1",
            "iss": ISSUER,
            "aud": AUDIENCE,
            "iat": int(time.time()) - 7200,
            "exp": int(time.time()) - 3600,
        }
        token = jwt.encode(payload, SECRET, algorithm=ALGORITHM)
        with pytest.raises(UnauthorizedError, match="Invalid JWT signature"):
            verifier.verify_signature(token=token)

    def test_wrong_issuer(self):
        verifier = _make_verifier()
        payload = {
            "sub": "user1",
            "iss": "wrong-issuer",
            "aud": AUDIENCE,
            "iat": int(time.time()),
            "exp": int(time.time()) + 3600,
        }
        token = jwt.encode(payload, SECRET, algorithm=ALGORITHM)
        with pytest.raises(UnauthorizedError, match="Invalid JWT signature"):
            verifier.verify_signature(token=token)


class TestVerify:
    def test_source_claim_match(self):
        verifier = _make_verifier()
        token = _encode_token({"source": "jitsi", "type": "conference.joined", "room": "abc"})
        claims = verifier.verify(token=token, event_source="jitsi", event_type="conference.joined")
        assert claims["room"] == "abc"
        assert "source" not in claims
        assert "type" not in claims

    def test_source_claim_mismatch_raises_unauthorized(self):
        verifier = _make_verifier()
        token = _encode_token({"source": "jitsi", "type": "conference.joined"})
        with pytest.raises(UnauthorizedError, match="source claim does not match"):
            verifier.verify(token=token, event_source="other-source", event_type="conference.joined")

    def test_type_claim_mismatch_raises_unauthorized(self):
        verifier = _make_verifier()
        token = _encode_token({"source": "jitsi", "type": "conference.joined"})
        with pytest.raises(UnauthorizedError, match="type claim does not match"):
            verifier.verify(token=token, event_source="jitsi", event_type="other.type")

    def test_no_source_claim_allows_any_source(self):
        verifier = _make_verifier()
        token = _encode_token({"room": "abc"})
        claims = verifier.verify(token=token, event_source="anything", event_type="anything")
        assert claims["room"] == "abc"

    def test_pre_decoded_claims(self):
        verifier = _make_verifier()
        claims = verifier.verify(
            claims={"source": "jitsi", "type": "conf.joined", "room": "r1", "exp": 0, "iat": 0},
            event_source="jitsi",
            event_type="conf.joined",
        )
        assert claims["room"] == "r1"

    def test_missing_token_and_claims_raises_unauthorized(self):
        verifier = _make_verifier()
        with pytest.raises(UnauthorizedError, match="Missing authorization token"):
            verifier.verify(event_source="x", event_type="y")
```

- [ ] **Step 2: Run security tests**

Run: `cd /Users/alexandrlelikov/PycharmProjects/events/event-receiver && python -m pytest tests/test_security.py -v`
Expected: All tests PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_security.py
git commit -m "test: add JWT verification unit tests

10 tests covering verify_signature (valid, invalid sig, expired,
wrong issuer) and verify (claim match/mismatch, pre-decoded claims,
missing token)."
```

---

### Task 7: Add normalizer tests

**Files:**
- Create: `tests/test_normalizers.py`

- [ ] **Step 1: Write normalizer tests**

Create `tests/test_normalizers.py`:

```python
from unittest.mock import MagicMock

from event_schemas.types import EventType

from event_receiver.normalizers import normalize_event_payload


class TestBookingCreatedNormalizer:
    def test_extracts_organizer_and_client(self):
        payload = {
            "user": {"email": "org@test.com"},
            "client": {"email": "cli@test.com"},
            "start_time": "2026-01-01T10:00:00Z",
            "end_time": "2026-01-01T11:00:00Z",
        }
        result = normalize_event_payload(EventType.BOOKING_CREATED, payload)
        participants = result["normalized"]["participants"]
        assert len(participants) == 2
        assert {"email": "org@test.com", "role": "organizer"} in participants
        assert {"email": "cli@test.com", "role": "client"} in participants

    def test_preserves_original_payload(self):
        payload = {"user": {"email": "a@b.com"}, "client": {"email": "c@d.com"}, "start_time": "x", "end_time": "y"}
        result = normalize_event_payload(EventType.BOOKING_CREATED, payload)
        assert result["original"] is payload


class TestBookingReassignedNormalizer:
    def test_extracts_new_organizer(self):
        payload = {
            "user": {"email": "new@org.com", "time_zone": "Europe/Moscow"},
            "previous_organizer": {"email": "old@org.com"},
        }
        result = normalize_event_payload(EventType.BOOKING_REASSIGNED, payload)
        participants = result["normalized"]["participants"]
        assert len(participants) == 1
        assert participants[0]["email"] == "new@org.com"
        assert participants[0]["role"] == "organizer"


class TestBookingReminderSentNormalizer:
    def test_extracts_email(self):
        payload = {"email": "user@test.com"}
        result = normalize_event_payload(EventType.BOOKING_REMINDER_SENT, payload)
        participants = result["normalized"]["participants"]
        assert len(participants) == 1
        assert participants[0]["email"] == "user@test.com"


class TestGetStreamNormalizer:
    def test_extracts_user_from_getstream_event(self):
        decoder = MagicMock(return_value="decoded@email.com")
        payload = {
            "type": "message.new",
            "user": {"id": "encrypted_id"},
            "members": [{"user_id": "encrypted_id", "role": "owner"}],
        }
        result = normalize_event_payload(EventType.GETSTREAM_MESSAGE_NEW, payload, getstream_decoder=decoder)
        participants = result["normalized"]["participants"]
        assert len(participants) == 1
        assert participants[0]["email"] == "decoded@email.com"
        assert participants[0]["role"] == "organizer"
        decoder.assert_called_once_with("encrypted_id")

    def test_client_role_for_non_owner(self):
        decoder = MagicMock(return_value="user@test.com")
        payload = {
            "type": "message.new",
            "user": {"id": "enc_id"},
            "members": [{"user_id": "enc_id", "role": "member"}],
        }
        result = normalize_event_payload(EventType.GETSTREAM_MESSAGE_NEW, payload, getstream_decoder=decoder)
        assert result["normalized"]["participants"][0]["role"] == "client"


class TestMalformedPayloads:
    def test_invalid_payload_returns_empty_participants(self):
        result = normalize_event_payload(EventType.BOOKING_CREATED, {"garbage": True})
        assert result["normalized"]["participants"] == []
        assert result["original"] == {"garbage": True}

    def test_unknown_event_type_returns_empty_participants(self):
        result = normalize_event_payload(EventType.BOOKING_RESCHEDULED, {"some": "data"})
        assert result["normalized"]["participants"] == []

    def test_empty_payload_returns_empty_participants(self):
        result = normalize_event_payload(EventType.BOOKING_REMINDER_SENT, {})
        assert result["normalized"]["participants"] == []
```

- [ ] **Step 2: Run normalizer tests**

Run: `cd /Users/alexandrlelikov/PycharmProjects/events/event-receiver && python -m pytest tests/test_normalizers.py -v`
Expected: All tests PASS

- [ ] **Step 3: Run all tests together**

Run: `cd /Users/alexandrlelikov/PycharmProjects/events/event-receiver && python -m pytest tests/ -v`
Expected: All tests from test_routing.py, test_security.py, test_normalizers.py PASS

- [ ] **Step 4: Commit**

```bash
git add tests/test_normalizers.py
git commit -m "test: add normalizer unit tests

11 tests covering participant extraction for booking created/
reassigned/reminder_sent, GetStream events with role mapping,
and malformed/empty payload handling."
```

---

### Task 8: Final lint check

- [ ] **Step 1: Run ruff on entire codebase**

Run: `cd /Users/alexandrlelikov/PycharmProjects/events/event-receiver && ruff check . && ruff format --check .`
Expected: No errors. If there are lint issues in test files, fix them.

- [ ] **Step 2: Fix any lint issues and commit if needed**

If ruff found issues, fix them and commit:
```bash
git add -u
git commit -m "style: fix lint issues from ruff"
```
