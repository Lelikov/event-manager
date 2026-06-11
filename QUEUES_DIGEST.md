# Queues Digest

Актуальная маршрутизация событий в RabbitMQ (по `event_receiver/config.py`).

## Сводная таблица

| Queue | Source Pattern | Type Pattern | Events |
|---|---|---|---|
| `events.booking.lifecycle` (routing key) | `booking` | `booking.created` / `booking.rescheduled` / `booking.reassigned` / `booking.cancelled` / `booking.rejected` / `booking.reminder_sent` | lifecycle бронирования |
| `events.chat.lifecycle` | `booking` | `chat.created` / `chat.deleted` | lifecycle чата |
| `events.chat.activity` | `booking` | `chat.message_sent` | активность в чате |
| `events.meeting.lifecycle` | `booking` | `meeting.url_created` / `meeting.url_deleted` | lifecycle meeting URL |
| `events.notification.delivery` | `*` | `notification.email.message_sent` / `notification.telegram.message_sent` | отправка уведомлений |
| `events.notification.delivery` | `*` | `notification.push.message_sent` | результат отправки push |
| `events.notification.commands` | `*` | `notification.send_requested` | команды для event-notifier |
| `events.jitsi` | `jitsi*` | `*` | все Jitsi-события |
| `events.mail` | `unisender-go` | `unisender.*` | события UniSender |
| `events.chat` | `getstream` | `getstream.*` | события GetStream |
| `events.user.email` | `admin` | `user.email.*` | запросы смены email клиента |
| `events.unrouted` | fallback | fallback | все события без match по rules |

## events.booking.lifecycle

События жизненного цикла бронирования:
- `booking.created`
- `booking.rescheduled`
- `booking.reassigned`
- `booking.cancelled`
- `booking.rejected` — (от event-booking при нарушении constraint validation)
- `booking.reminder_sent` — (зарезервировано, продюсера сейчас нет)

К routing key `events.booking.lifecycle` привязаны ДВЕ очереди (fan-out, по одной на консьюмера):
- `events.booking.lifecycle.saver` — event-saver
- `events.booking.lifecycle.booking` — event-booking

Очередь `events.booking.reminder` удалена (не имела ни продюсера, ни консьюмера);
напоминания идут через `notification.send_requested` (trigger `BOOKING_REMINDER`).

## events.chat.lifecycle

События жизненного цикла чата:
- `chat.created`
- `chat.deleted`

## events.chat.activity

События активности в чате:
- `chat.message_sent`

## events.meeting.lifecycle

События жизненного цикла meeting URL:
- `meeting.url_created`
- `meeting.url_deleted`

## events.notification.delivery

События отправки уведомлений:
- `notification.email.message_sent`
- `notification.telegram.message_sent`

## events.jitsi

Все события Jitsi:
- `source_pattern = "jitsi*"`
- `type_pattern = "*"`

## events.mail

События UniSender:
- `source_pattern = "unisender-go"`
- `type_pattern = "unisender.*"`

## events.chat

События GetStream:
- `source_pattern = "getstream"`
- `type_pattern = "getstream.*"`

## events.user.email

События запроса смены email клиента:
- `source_pattern = "admin"`
- `type_pattern = "user.email.*"`

Поступают через эндпоинт `POST /event/admin` (auth: static API key в заголовке `Authorization`).
Потребитель: `event-users` (FastStream RabbitMQ consumer).

## events.unrouted

Fallback-очередь по умолчанию:
- попадают события, которые не совпали ни с одним routing rule.

---

## Payload событий для `/event/booking`

Ниже разделено на два уровня:
- **Входящий payload** в `/event/booking` (контракт источника, `EVENTS_DIGEST.md`)
- **Исходящее сообщение в RabbitMQ** после `ingest_booking` + `CloudEventPublisher`

### Важно: что модифицируется в `ingest_booking`

В `event_receiver/controllers/ingest.py` делается:
- `booking_uid = incoming.data.pop("booking_uid")`
- дальше в publisher уходит:
  - `booking_id=booking_uid` (в CloudEvent attributes)
  - `data=incoming.data` (то есть **без** `booking_uid`)

Итог: для booking endpoint `booking_uid` **не остаётся в data/payload**, а переносится в CloudEvent-атрибут `booking_id`.

### Что уходит в headers, а что в payload (RabbitMQ)

По `event_receiver/adapters/publisher.py`:
- формируется CloudEvent через `to_binary(event)`;
- в RabbitMQ публикуется:
  - `body` = data payload события;
  - `headers` = CloudEvent binary headers (`ce-*`),
  - `content-type` вынимается отдельно в параметр `content_type`.

Для booking-событий обычно так:
- **Headers**: `ce-type`, `ce-source`, `ce-id`, `ce-time`, `ce-bookingid`, `ce-specversion` (+ прочие системные при необходимости)
- **Payload (body/data)**: поля события **кроме** `booking_uid`.

### Входящий payload `/event/booking` (контракт источника)

### booking.reminder_sent
- `booking_uid: str`
- `email: str`

### booking.created
- `booking_uid: str`
- `users: list[{email, role: organizer|client|guest, time_zone?}]` — ровно один organizer,
  ≥1 client/guest; guest нормализуется в role `client`; ВСЕ участники попадают
  в `normalized.participants` (multi-attendee/seated бронирования поддерживаются)
- исходящий payload: `user{email,time_zone?}` / `client{email,time_zone?}` (первичная пара),
  `users[]` (полный список), `start_time: datetime`, `end_time: datetime`

### booking.rescheduled
- `booking_uid: str`
- `start_time: datetime`
- `end_time: datetime`
- `previous_booking.start_time: datetime | None`

### booking.reassigned
- `booking_uid: str`
- `previous_organizer.email: str | None`
- `user.email: str`
- `user.time_zone: str`

### booking.cancelled
- `booking_uid: str`
- `cancellation_reason: str | None`

### chat.created
- `booking_uid: str`
- `organizer_id: str`
- `client_id: str`

### chat.deleted
- `booking_uid: str`

### chat.message_sent
- `booking_uid: str`
- `user_id: str`

### meeting.url_created
- `booking_uid: str`
- `email: str`
- `recipient_role: "client" | "organizer"`
- `meeting_url: str`

### meeting.url_deleted
- `booking_uid: str`
- `recipient_role: "client" | "organizer"`

### notification.telegram.message_sent
- `booking_uid: str`
- `email: str`
- `recipient_role: "organizer"`
- `trigger_event: TriggerEvent`

### notification.email.message_sent (базовый кейс)
- `booking_uid: str`
- `email: str`
- `job_id: str | None`
- `recipient_role: "organizer" | "client"`
- `trigger_event: TriggerEvent`

### notification.email.message_sent (`notify_client_booking_rejected`)
- `booking_uid: str`
- `job_id: str | None`
- `client_email: str`
- `available_from: datetime`
- `has_active_booking: bool`
- `active_booking_start: datetime | None`
- `previous_meeting_dates: list[datetime]`
- `rejection_reasons: list[str]`
- `trigger_event: TriggerEvent (BOOKING_REJECTED)`

### Исходящий payload (body/data) в RabbitMQ для booking endpoint

Для всех событий выше действует правило:
- `booking_uid` переносится в header `ce-bookingid`;
- в `body` остаются остальные поля из списка соответствующего события.

## events.notification.commands

Очередь команд для event-notifier сервиса:
- `notification.send_requested` — запрос на отправку уведомлений по всем каналам

**Продюсер:** `event-booking` (от orchestration service при processing booking events)
**Консьюмер:** `event-notifier`
**Source pattern:** `*` (любой сервис может отправить команду)
**Типовой источник:** `booking` (от event-booking)

## Каноническая топология (audit-v2)

Источник истины — `event_schemas.queues` (`ALL_QUEUES`, `ROUTING_RULES`):

- exchange `events` (topic, durable), DLX `events.dlx` (topic, durable);
- аргументы каждой очереди (verbatim): `x-max-priority=10`,
  `x-dead-letter-exchange=events.dlx`, `x-dead-letter-routing-key=<queue>.dlq`;
- для каждой очереди объявляется `<queue>.dlq` (`x-message-ttl=86400000`), привязанная к `events.dlx`;
- event-receiver объявляет ПОЛНУЮ топологию на старте; каждый консьюмер
  идемпотентно объявляет свои очереди с теми же аргументами;
- одна очередь = один консьюмер; fan-out — через несколько очередей на один routing key;
- неизвестные `EventType` больше не дают 500: публикуются в `events.unrouted`;
- на старте event-receiver валидирует, что каждый routing destination имеет
  очередь с соответствующим binding (иначе fail-fast `ConfigurationError`);
- publish выполняется с confirm-таймаутом (`PUBLISH_TIMEOUT`, по умолчанию 10s)
  и `on_return_raises=True`: таймаут/unroutable → HTTP 503, источник ретраит.

### DLQ: окно потери 24 часа (известное ограничение)

`<queue>.dlq` имеет `x-message-ttl=86400000` (24h) и НЕ имеет собственного
dead-letter-exchange — ни один сервис не консьюмит `*.dlq`. Сообщение, попавшее
в DLQ (nack консьюмера после ошибки парсинга/записи), безвозвратно удаляется
через 24 часа. Аргументы каноничны в `event_schemas.queues` (CONTRACT_DECISIONS D2),
менять их локально нельзя. Операционные требования:
- алертинг на глубину `*.dlq` (depth > 0 — инцидент, есть максимум 24h на redrive);
- redrive вручную: shovel/`rabbitmqadmin` из `<queue>.dlq` обратно в exchange `events`
  с routing key `<queue>` после устранения причины nack.

### Ingress cal.com (`/event/calcom`)

Нативные webhooks cal.com транслируются в канонические `booking.*` события
(source `booking` → routing rules booking lifecycle). Неизвестные `triggerEvent`
публикуются как `calcom.<trigger>` (source `calcom`) → `events.unrouted`.
