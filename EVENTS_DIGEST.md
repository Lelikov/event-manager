# Events Digest

## Общий контракт

- `booking_uid`: `str` (всегда присутствует в payload)
- `event`: `EventType`
- `data`: `dict[str, Any] | None`

---

## booking.reminder_sent

| Поле        | Тип   |
|-------------|-------|
| booking_uid | `str` |
| email       | `str` |

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

## booking.rescheduled

| Поле                        | Тип        |
|-----------------------------|------------|
| booking_uid                 | `str`      |
| start_time                  | `datetime` |
| end_time                    | `datetime` |
| previous_booking.start_time | `datetime  | None` |

## booking.reassigned

| Поле                     | Тип   |
|--------------------------|-------|
| booking_uid              | `str` |
| previous_organizer.email | `str  | None` |
| user.email               | `str` |
| user.time_zone           | `str` |

## booking.cancelled

| Поле                | Тип   |
|---------------------|-------|
| booking_uid         | `str` |
| cancellation_reason | `str  | None` |

## chat.created

| Поле         | Тип   |
|--------------|-------|
| booking_uid  | `str` |
| organizer_id | `str` |
| client_id    | `str` |

## chat.deleted

| Поле        | Тип   |
|-------------|-------|
| booking_uid | `str` |

## chat.message_sent

| Поле        | Тип   |
|-------------|-------|
| booking_uid | `str` |
| user_id     | `str` |

## meeting.url_created

| Поле           | Тип       |
|----------------|-----------|
| booking_uid    | `str`     |
| email          | `str`     |
| recipient_role | `"client" | "organizer"` |
| meeting_url    | `str`     |

## meeting.url_deleted

| Поле           | Тип       |
|----------------|-----------|
| booking_uid    | `str`     |
| recipient_role | `"client" | "organizer"` |

## notification.telegram.message_sent

| Поле           | Тип            |
|----------------|----------------|
| booking_uid    | `str`          |
| email          | `str`          |
| recipient_role | `"organizer"`  |
| trigger_event  | `TriggerEvent` |

## notification.email.message_sent

### Базовый кейс (обычные email-уведомления)

| Поле           | Тип            |
|----------------|----------------|
| booking_uid    | `str`          |
| email          | `str`          |
| job_id         | `str           | None` |
| recipient_role | `"organizer"   | "client"` |
| trigger_event  | `TriggerEvent` |

### Кейс отклонённого бронирования (`notify_client_booking_rejected`)

| Поле                   | Тип                                 |
|------------------------|-------------------------------------|
| booking_uid            | `str`                               |
| job_id                 | `str                                | None` |
| client_email           | `str`                               |
| available_from         | `datetime`                          |
| has_active_booking     | `bool`                              |
| active_booking_start   | `datetime                           | None` |
| previous_meeting_dates | `list[datetime]`                    |
| rejection_reasons      | `list[str]`                         |
| trigger_event          | `TriggerEvent` (`BOOKING_REJECTED`) |
