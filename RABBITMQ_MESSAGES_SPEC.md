# RabbitMQ Messages Specification

Спецификация сообщений, публикуемых `event-receiver` в RabbitMQ после обработки и нормализации входящих событий.

## Общий формат

Все сообщения публикуются в **CloudEvents binary format** (RFC spec v1.0):
- Атрибуты события передаются как **AMQP-заголовки** с префиксом `ce-`
- Тело сообщения содержит **JSON** с нормализованной полезной нагрузкой
- `content-type`: `application/json`

### CloudEvents заголовки (AMQP headers)

| Заголовок | Тип | Описание |
|---|---|---|
| `ce-specversion` | string | Всегда `"1.0"` |
| `ce-type` | string | Тип события (см. таблицы ниже) |
| `ce-source` | string | Источник события (`booking`, `unisender-go`, `getstream`, `jitsi*`) |
| `ce-id` | string | ID события из оригинального CloudEvent (если отсутствует — не передаётся) |
| `ce-time` | string | Время события ISO 8601 (если отсутствует — не передаётся) |
| `ce-traceid` | string | UUID v4 — trace ID для distributed tracing (из заголовка запроса или сгенерированный) |
| `ce-spanid` | string | UUID v4 — span ID (генерируется каждый раз) |
| `ce-idempotencykey` | string | SHA-256 хеш от `event_type + booking_id + sorted(data)` — ключ идемпотентности |
| `ce-dataschema` | string | `https://schemas.example.com/{event_type}/{schema_version}` (сейчас всегда `v1`) |
| `ce-datacontenttype` | string | `"application/json"` |
| `ce-publisherservice` | string | `"event-receiver"` |
| `ce-publisherversion` | string | `"0.1.0"` |
| `ce-booking_id` | string | ID бронирования (если доступен) |

Trace ID извлекается из входящего HTTP-запроса в порядке приоритета: `X-Trace-Id` → `X-Request-Id` → `traceparent` (W3C). Если ни один не найден — генерируется.

### Структура тела сообщения

Каждое сообщение содержит нормализованную обёртку:

```json
{
  "original": { /* оригинальная полезная нагрузка без изменений */ },
  "normalized": {
    "participants": [
      {
        "email": "user@example.com",                       // обязательно
        "role": "organizer",                               // "organizer" | "client"
        "user_id": "550e8400-e29b-41d4-a716-446655440000", // UUID из event-users (обязательно)
        "time_zone": "Europe/Moscow"                       // опционально, только если присутствует
      }
    ]
  }
}
```

`user_id` — UUID пользователя из сервиса **event-users**. Перед публикацией event-receiver выполняет lookup по `(email, role)`: если пользователь не найден — создаёт его. Поле присутствует у каждого участника в списке.

Если участников извлечь не удалось (ошибка валидации, отсутствующие поля), `participants` будет пустым массивом.

---

## RabbitMQ топология

- **Exchange**: `events` (topic, durable)
- **Dead Letter Exchange**: `events.dlx` (topic, durable)
- Каждая очередь поддерживает **приоритеты 0–10** (`x-max-priority: 10`)
- Недоставленные сообщения уходят в DLQ `{queue_name}.dlq` с TTL 24 часа

---

## Очереди и события

### `events.booking.lifecycle`
> Lifecycle-события бронирований. **Приоритет: CRITICAL (10)**

#### `booking.created`
- **Источник**: `POST /event/booking`, source `booking`
- **Routing key**: `events.booking.lifecycle`

**Тело `original`:**
```json
{
  "user": { "email": "organizer@example.com" },
  "client": { "email": "client@example.com" },
  "start_time": "2026-04-09T06:05:00+00:00",
  "end_time": "2026-04-09T06:10:00+00:00"
}
```
> Входящий запрос содержит `users[{role: organizer}, {role: client}]` + `booking_uid`. Контроллер трансформирует в структуру `user`/`client`, `booking_uid` выносится в `ce-booking_id`.

**Нормализованные участники:**
```json
[
  { "email": "organizer@example.com", "role": "organizer", "user_id": "550e8400-e29b-41d4-a716-446655440000" },
  { "email": "client@example.com", "role": "client", "user_id": "661f9511-f3ac-52e5-b827-557766551111" }
]
```

---

#### `booking.rescheduled`
- **Источник**: `POST /event/booking`, source `booking`

**Тело `original`:**
```json
{
  "start_time": "2026-04-10T10:00:00+00:00",
  "end_time": "2026-04-10T11:00:00+00:00",
  "previous_booking": {
    "start_time": "2026-04-09T10:00:00+00:00"
  }
}
```

**Нормализованные участники:** `[]` (не извлекаются)

---

#### `booking.reassigned`
- **Источник**: `POST /event/booking`, source `booking`

**Тело `original`:**
```json
{
  "previous_organizer": { "email": "old@example.com" },
  "user": { "email": "new@example.com", "time_zone": "Europe/Moscow" }
}
```

**Нормализованные участники:**
```json
[
  { "email": "new@example.com", "role": "organizer", "user_id": "550e8400-e29b-41d4-a716-446655440000", "time_zone": "Europe/Moscow" }
]
```

---

#### `booking.cancelled`
- **Источник**: `POST /event/booking`, source `booking`

**Тело `original`:**
```json
{
  "cancellation_reason": "Client request"
}
```

**Нормализованные участники:** `[]` (полезная нагрузка не содержит структуру `user`/`client`)

---

### `events.booking.reminder`
> Напоминания о бронированиях. **Приоритет: HIGH (7)**

#### `booking.reminder_sent`
- **Источник**: `POST /event/booking`, source `booking`

**Тело `original`:**
```json
{
  "email": "client@example.com"
}
```

**Нормализованные участники:**
```json
[
  { "email": "client@example.com", "role": "client", "user_id": "661f9511-f3ac-52e5-b827-557766551111" }
]
```

---

### `events.meeting.lifecycle`
> События создания/удаления ссылок на встречи. **Приоритет: NORMAL (5)**

#### `meeting.url_created`
- **Источник**: `POST /event/booking`, source `booking`

**Тело `original`:**
```json
{
  "users": [
    { "email": "organizer@example.com", "role": "organizer" }
  ],
  "meeting_url": "https://meet.example.com/booking-123"
}
```

**Нормализованные участники:**
```json
[
  { "email": "organizer@example.com", "role": "organizer", "user_id": "550e8400-e29b-41d4-a716-446655440000" }
]
```

---

#### `meeting.url_deleted`
- **Источник**: `POST /event/booking`, source `booking`

**Тело `original`:**
```json
{
  "users": [
    { "email": "organizer@example.com", "role": "organizer" }
  ]
}
```

**Нормализованные участники:** аналогично `meeting.url_created`

---

### `events.notification.delivery`
> Подтверждения отправки уведомлений. **Приоритет: HIGH (7)**

#### `notification.email.message_sent`
- **Источник**: `POST /event/booking`, source `booking`

**Тело `original`:**
```json
{
  "users": [
    { "email": "organizer@example.com", "role": "organizer" }
  ],
  "job_id": "1wAEiO-0018x4-GTuG",
  "trigger_event": "BOOKING_CREATED"
}
```

**Нормализованные участники:**
```json
[
  { "email": "organizer@example.com", "role": "organizer", "user_id": "550e8400-e29b-41d4-a716-446655440000" }
]
```

---

#### `notification.telegram.message_sent`
- **Источник**: `POST /event/booking`, source `booking`

**Тело `original`:**
```json
{
  "users": [
    { "email": "organizer@example.com", "role": "organizer" }
  ],
  "trigger_event": "BOOKING_CREATED"
}
```

**Нормализованные участники:** аналогично email

---

### `events.chat.lifecycle`
> Lifecycle-события чатов. **Приоритет: NORMAL (5)**

#### `chat.created`
- **Источник**: `POST /event/booking`, source `booking`

**Тело `original`:**
```json
{
  "organizer_id": "getstream-user-id-1",
  "client_id": "getstream-user-id-2"
}
```

**Нормализованные участники:** `[]`

---

#### `chat.deleted`
- **Источник**: `POST /event/booking`, source `booking`

**Тело `original`:** `{}`

**Нормализованные участники:** `[]`

---

### `events.chat.activity`
> Активность в чатах (сообщения). **Приоритет: NORMAL (5)**

#### `chat.message_sent`
- **Источник**: `POST /event/booking`, source `booking`

**Тело `original`:**
```json
{
  "user_id": "getstream-user-id"
}
```

**Нормализованные участники:** `[]`

---

### `events.mail`
> Статусы транзакционной email-рассылки UniSender Go. **Приоритет: NORMAL (5)**

#### `unisender.events.v1.transactional.status.create`
- **Источник**: `POST /event/unisender-go` (HMAC MD5 подпись)
- **Source**: `unisender-go`
- **Routing key**: `events.mail`

**Тело `original`:**
```json
{
  "event_name": "transactional_email_status",
  "event_data": {
    "job_id": "1wAEAl-0018hY-AaTH",
    "email": "recipient@example.com",
    "status": "delivered",
    "event_time": "2026-04-07 21:41:26",
    "metadata": {
      "role": "organizer"
    }
  }
}
```
> `booking_uid` удаляется из `event_data.metadata` до публикации и выносится в `ce-booking_id`.

**Известные значения `status`:** `accepted`, `sent`, `delivered`, `soft_bounce`, `hard_bounce`, `unsubscribed`, `spam`

**Нормализованные участники:**
```json
[
  { "email": "recipient@example.com", "role": "organizer", "user_id": "550e8400-e29b-41d4-a716-446655440000" }
]
```
> `role` берётся из `event_data.metadata.role` — `"organizer"` или `"client"`.

---

### `events.chat`
> Webhook-события GetStream (чат). **Приоритет: NORMAL (5)**

Все события публикуются с source `getstream`, routing key `events.chat`.
Тела сообщений — сырые GetStream webhook payload без изменений.

#### `getstream.channel.created`

**Тело `original`:**
```json
{
  "type": "channel.created",
  "created_at": "2026-04-07T22:14:55.784401822Z",
  "cid": "messaging:dLjsaAEFxQk9zPkqczF2Hn",
  "channel_id": "dLjsaAEFxQk9zPkqczF2Hn",
  "channel_type": "messaging",
  "channel_member_count": 2,
  "channel": { /* полный объект канала GetStream */ },
  "members": [ /* список участников */ ],
  "user": { "id": "<encrypted_email>", "name": "...", "role": "user", ... }
}
```

**Нормализованные участники:**
```json
[
  { "email": "user@example.com", "role": "organizer", "user_id": "550e8400-e29b-41d4-a716-446655440000" }
]
```
> `user.id` — AES-CBC зашифрованный email, дешифруется на стороне `event-receiver`. `role` определяется по позиции в `members`: `owner` → `"organizer"`, остальные → `"client"`.

---

#### `getstream.message.new`

**Тело `original`:**
```json
{
  "type": "message.new",
  "created_at": "2026-04-07T22:14:56.287597046Z",
  "cid": "messaging:dLjsaAEFxQk9zPkqczF2Hn",
  "channel_id": "dLjsaAEFxQk9zPkqczF2Hn",
  "channel_type": "messaging",
  "message_id": "7ff05c0b-ca96-4d55-95f0-bff6ff331185",
  "message": {
    "id": "7ff05c0b-ca96-4d55-95f0-bff6ff331185",
    "text": "...",
    "html": "<p>...</p>",
    "type": "regular",
    "user": { "id": "<encrypted_email>", ... }
  },
  "user": { "id": "<encrypted_email>", "name": "...", "role": "user", ... },
  "members": [ ... ]
}
```

**Нормализованные участники:** аналогично `channel.created`

---

#### `getstream.message.read`

**Тело `original`:**
```json
{
  "type": "message.read",
  "created_at": "2026-04-07T22:22:34.500684556Z",
  "cid": "messaging:62tnJqeMXi2oSVc6TzPmPa",
  "channel_id": "62tnJqeMXi2oSVc6TzPmPa",
  "channel_type": "messaging",
  "user": {
    "id": "<encrypted_email>",
    "name": "...",
    "total_unread_count": 134,
    "unread_count": 134,
    "channel_unread_count": 0,
    ...
  },
  "last_read_message_id": "700a557e-1b0c-4a58-9a8b-d247ecb6e485"
}
```

**Нормализованные участники:** аналогично `channel.created`

---

#### `getstream.message.updated`, `getstream.message.deleted`, `getstream.channel.deleted`

Структура аналогична остальным GetStream событиям. `channel_id` используется как `booking_id` (`ce-booking_id`).

---

### `events.jitsi`
> Jitsi webhook события. **Приоритет: NORMAL (5)**

Все события источника `jitsi*` маршрутизируются в эту очередь.
Авторизация через JWT (HS256). JWT claims мержатся в данные события.

#### `jitsi.room.created`, `jitsi.participant.joined`, `jitsi.participant.left`
- **Источник**: `POST /event/jitsi`, source из CE-заголовка (паттерн `jitsi*`)
- `ce-booking_id`: значение `room` из JWT claims

**Тело `original`:**
```json
{
  /* поля из тела CloudEvent */
  "room": "booking-uid",
  "event_type": "room.created",
  /* + JWT claims (sub, iss, aud, room, context, etc.) */
  "context": {
    "user": {
      "email": "participant@example.com",
      "role": "organizer",
      "name": "John Doe"
    }
  }
}
```

**Нормализованные участники:**
```json
[
  { "email": "participant@example.com", "role": "organizer", "user_id": "550e8400-e29b-41d4-a716-446655440000" }
]
```
> `email` и `role` берутся из `context.user` в JWT claims.

---

### `events.unrouted`
> Fallback-очередь для событий, не попавших ни под одно правило маршрутизации.

---

## Dead Letter Queues

Для каждой очереди `{queue}` существует DLQ `{queue}.dlq`:
- Привязана к exchange `events.dlx`
- TTL сообщений: **24 часа**
- Сообщения попадают сюда при rejected/nacked без повторной постановки в очередь

| Очередь | DLQ |
|---|---|
| `events.booking.lifecycle` | `events.booking.lifecycle.dlq` |
| `events.booking.reminder` | `events.booking.reminder.dlq` |
| `events.meeting.lifecycle` | `events.meeting.lifecycle.dlq` |
| `events.notification.delivery` | `events.notification.delivery.dlq` |
| `events.chat.lifecycle` | `events.chat.lifecycle.dlq` |
| `events.chat.activity` | `events.chat.activity.dlq` |
| `events.mail` | `events.mail.dlq` |
| `events.chat` | `events.chat.dlq` |
| `events.jitsi` | `events.jitsi.dlq` |
| `events.unrouted` | `events.unrouted.dlq` |

---

## Сводная таблица

| Очередь | Event type | Source | Приоритет |
|---|---|---|---|
| `events.booking.lifecycle` | `booking.created` | `booking` | CRITICAL (10) |
| `events.booking.lifecycle` | `booking.rescheduled` | `booking` | CRITICAL (10) |
| `events.booking.lifecycle` | `booking.reassigned` | `booking` | CRITICAL (10) |
| `events.booking.lifecycle` | `booking.cancelled` | `booking` | CRITICAL (10) |
| `events.booking.reminder` | `booking.reminder_sent` | `booking` | HIGH (7) |
| `events.meeting.lifecycle` | `meeting.url_created` | `booking` | NORMAL (5) |
| `events.meeting.lifecycle` | `meeting.url_deleted` | `booking` | NORMAL (5) |
| `events.notification.delivery` | `notification.email.message_sent` | `booking` | HIGH (7) |
| `events.notification.delivery` | `notification.telegram.message_sent` | `booking` | HIGH (7) |
| `events.chat.lifecycle` | `chat.created` | `booking` | NORMAL (5) |
| `events.chat.lifecycle` | `chat.deleted` | `booking` | NORMAL (5) |
| `events.chat.activity` | `chat.message_sent` | `booking` | NORMAL (5) |
| `events.mail` | `unisender.events.v1.transactional.status.create` | `unisender-go` | NORMAL (5) |
| `events.chat` | `getstream.channel.created` | `getstream` | NORMAL (5) |
| `events.chat` | `getstream.channel.deleted` | `getstream` | NORMAL (5) |
| `events.chat` | `getstream.message.new` | `getstream` | NORMAL (5) |
| `events.chat` | `getstream.message.updated` | `getstream` | NORMAL (5) |
| `events.chat` | `getstream.message.deleted` | `getstream` | NORMAL (5) |
| `events.chat` | `getstream.message.read` | `getstream` | NORMAL (5) |
| `events.jitsi` | `jitsi.room.created` | `jitsi*` | NORMAL (5) |
| `events.jitsi` | `jitsi.participant.joined` | `jitsi*` | NORMAL (5) |
| `events.jitsi` | `jitsi.participant.left` | `jitsi*` | NORMAL (5) |
