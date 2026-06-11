# event-receiver Audit Findings — audit-v2

Audited: 2026-06-10 · Fixed: 2026-06-11 (branch `audit-fixes`)

Policy: fix everything. Every finding below was verified against the code on `audit-fixes`
(after the contracts-coordinator wave) and either fixed with a test or explicitly dispositioned.
Previous audit (2026-04-19) findings that were still open are folded in with origin `april`.

---

## Resolved

### HIGH

| Finding | Resolution |
|---|---|
| Publisher crashes with `ValueError` on any event type not in the shared `EventType` enum — 500 + lost message | Fixed by the contracts wave (`_coerce_event_type` → unknown types route to `events.unrouted`, priority NORMAL, payload preserved). Regression test added here: publishing `getstream.health.check` succeeds and routes to `events.unrouted` (`tests/test_publisher.py`) |
| No cal.com ingress path exists — `/event/booking` has no production producer; cal.com webhook format unsupported | New `POST /event/calcom` endpoint: verifies `X-Cal-Signature-256` (HMAC-SHA256, constant-time), translates `BOOKING_CREATED`/`BOOKING_CANCELLED`/`BOOKING_RESCHEDULED` to canonical D5 payloads (`organizer`+`attendees`+`responses.guests` → `users[]`, `startTime`/`endTime` → `start_time`/`end_time`, `uid` → `bookingid`, `rescheduleUid` → `previous_booking_uid`); unknown triggers → `calcom.<trigger>` → `events.unrouted`. Validated against real samples from `requests.jsonl` (`event_receiver/calcom.py`, `tests/test_calcom.py`) |
| No idempotency enforcement — duplicate webhook delivery produces duplicate RabbitMQ messages (april H-6) | Short-TTL (10 min) bounded in-memory cache keyed on the existing `idempotencykey` suppresses retry duplicates before publish; only successful publishes are remembered, so the UniSender mid-batch-failure retry republishes only the unpublished suffix of identical events. Authoritative dedup remains event-saver's DB constraint over the same identity |

### MEDIUM

| Finding | Resolution |
|---|---|
| Raw webhook bodies logged at INFO level in production (PII + UniSender auth signature) | `body=` removed from all INFO logs (jitsi/booking/unisender); tests assert bodies and signatures never reach the log stream |
| Malformed booking.created payload causes unhandled KeyError → HTTP 500 instead of 400 | Transform guards non-dict users entries and wraps required-field access; all malformed shapes → `BadRequestError` (400) with tests |
| RabbitMQ credentials logged in plain text at startup | Startup log now emits host/port/vhost only; test asserts password never appears |
| GetStream user-id 'encryption' uses AES-CBC with a fixed all-zero IV | Canonical scheme is now AES-GCM with random nonce (`encode_getstream_user_id`); decoder tries GCM, falls back to legacy CBC so event-booking can migrate without a flag day. Cross-service: encoder switch belongs to the event-booking fixer |
| event-schemas pinned via developer-machine absolute file:// path (april) | `[tool.uv.sources] event-schemas = { path = "../event-schemas", editable = true }` |
| No tests for IngestController or CloudEventPublisher | Full controller suite (auth/malformed/happy per endpoint), publisher suite (routing, unknown type, timeout, returns, dedup, enrichment), calcom suite, config suite — 103 tests total |
| Unroutable publishes silently dropped (`on_return_raises=False`) | Broker channel created with `on_return_raises=True`; `DeliveryError` → `PublishUnavailableError` → HTTP 503; `ensure_topology()` fails fast if any routing destination lacks a queue binding |
| No publish timeout — blocked RabbitMQ connection hangs every webhook request | `broker.publish(..., timeout=PUBLISH_TIMEOUT)` (default 10s); `TimeoutError` → `PublishUnavailableError` → HTTP 503 so sources back off and retry |
| event-users outage → participants permanently missing user_id, silently | Publisher emits a structured WARNING (`unresolved_emails`, `unresolved_count`) on every publish with unresolved user_ids; backfill job remains downstream scope (see Remaining) |
| Ingress booking.created hardwired to exactly one organizer + one client — guests/multi-attendee dropped | `users[]` is a true list: 1 organizer + N clients/guests (guest → role `client`); full list preserved in payload `users` and emitted into `normalized.participants`; canonical `user`/`client` primary pair kept per D5 |

### LOW

| Finding | Resolution |
|---|---|
| Dead code: unused `EventRouter.resolve_routing_key`, `_payload_digest`, dormant unverified-JWT-decode branch | All three deleted; `verify()` now requires pre-verified claims (signature-bypass path removed); interface and tests updated |
| API key comparison is not constant-time | `hmac.compare_digest` for `/event/booking` and `/event/admin` |
| Unhandled non-IngestError exceptions on authenticated-but-malformed input return raw 500 | Non-object CloudEvent data → 400 (jitsi/booking/admin); `ujson.loads` wrapped → 400 (unisender-go/getstream/calcom) |
| Style violation: else branch in ingest_booking | Replaced with guard-style assignment |
| Doc drift: `ce-booking_id` documented but actual header is `ce-bookingid`; spec claims id/time may be omitted | `RABBITMQ_MESSAGES_SPEC.md` and `docs/API_CONTRACTS.md` now document `ce-bookingid` and that the SDK always autogenerates `id`/`time` |
| CLAUDE.md documents non-existent `/event/cloudevents` and calls the service event-manager (april) | Removed; endpoint list now matches `routes.py` (incl. `/event/admin`, `/event/calcom`); name standardized to event-receiver |
| `/event/admin` undocumented in API_CONTRACTS.md and PROJECT_CONTEXT.md | Documented in both |
| Invalid LOG_LEVEL value crashes startup with obscure TypeError | `Settings` normalizes (upper/strip) and defaults unknown values to INFO with a warning; `main.py` falls back to INFO defensively |
| CORS origins and DEBUG middleware gating read os.environ directly, bypassing Settings/.env | `cors_origins` added to Settings; app factory (`create_app`) gates CORSMiddleware and RequestLoggerMiddleware from a Settings instance |
| StreamChat client constructed per request in ingest_getstream | Replaced with inline constant-time HMAC-SHA256 check; `stream-chat` dependency removed entirely |
| No secret-strength validation; weak/placeholder secrets accepted at startup (cross-service) | Outside `DEBUG`, every secret must be ≥16 chars, not single-character, and not contain placeholder markers — startup fails fast otherwise |

---

## Remaining (accepted / out of receiver scope)

| Item | Disposition |
|---|---|
| DLQs have a 24h TTL, no consumer, no further dead-lettering — dead letters destroyed after 24h | Queue arguments are canonical in `event_schemas.queues` (CONTRACT_DECISIONS D2/D8) and must not be changed per-service. Documented as an operational requirement in `QUEUES_DIGEST.md`: alert on `*.dlq` depth, manual redrive (shovel back to `events` with rk `<queue>`) within 24h. Changing the TTL/dead-lettering belongs to event-schemas |
| NULL `user_id` backfill / reconciliation | Degradation is now visible (structured warning) but the re-resolution job lives in event-saver/event-users — flagged for their fixers |
| Legacy CBC fallback in GetStream user-id decoder | Delete once event-booking switches its encoder to AES-GCM (`encode_getstream_user_id`) |
| Idempotency cache is per-process, in-memory | Acceptable: authoritative dedup is event-saver's DB constraint; cache only trims obvious webhook retries |

---

## New configuration introduced by the fixes

| Variable | Default | Purpose |
|---|---|---|
| `CALCOM_WEBHOOK_SECRET` | **required** | cal.com `X-Cal-Signature-256` verification |
| `PUBLISH_TIMEOUT` | `10.0` | RabbitMQ publish confirm timeout (s) |
| `CORS_ORIGINS` | `http://localhost:5173` | CORS allow-origins via Settings/.env |

Verification: `uv run pytest` (103 passed) and `uv run ruff check .` (clean) on every commit.
