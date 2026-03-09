# Queues Digest

Актуальная маршрутизация событий в RabbitMQ (по `event_receiver/config.py`).

## Сводная таблица

| Queue | Source Pattern | Type Pattern | Events |
|---|---|---|---|
| `events.booking.lifecycle` | `*` | `booking.created` / `booking.rescheduled` / `booking.reassigned` / `booking.cancelled` | lifecycle бронирования |
| `events.booking.reminder` | `*` | `booking.reminder_sent` | отправка напоминаний |
| `events.chat.lifecycle` | `*` | `chat.created` / `chat.deleted` | lifecycle чата |
| `events.chat.activity` | `*` | `chat.message_sent` | активность в чате |
| `events.meeting.lifecycle` | `*` | `meeting.url_created` / `meeting.url_deleted` | lifecycle meeting URL |
| `events.notification.delivery` | `*` | `notification.email.message_sent` / `notification.telegram.message_sent` | отправка уведомлений |
| `events.jitsi` | `jitsi*` | `*` | все Jitsi-события |
| `events.mail` | `unisender-go` | `unisender.*` | события UniSender |
| `events.chat` | `getstream` | `getstream.*` | события GetStream |
| `events.unrouted` | fallback | fallback | все события без match по rules |

## events.booking.lifecycle

События жизненного цикла бронирования:
- `booking.created`
- `booking.rescheduled`
- `booking.reassigned`
- `booking.cancelled`

## events.booking.reminder

События про отправку напоминаний:
- `booking.reminder_sent`

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
- **Headers**: `ce-type`, `ce-source`, `ce-id`, `ce-time`, `ce-booking_id`, `ce-specversion` (+ прочие системные при необходимости)
- **Payload (body/data)**: поля события **кроме** `booking_uid`.

### Входящий payload `/event/booking` (контракт источника)

### booking.reminder_sent
- `booking_uid: str`
- `email: str`

### booking.created
- `booking_uid: str`
- `user.email: str`
- `user.time_zone: str`
- `client.email: str`
- `client.time_zone: str`
- `start_time: datetime`
- `end_time: datetime`

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
- `booking_uid` переносится в header `ce-booking_id`;
- в `body` остаются остальные поля из списка соответствующего события.
