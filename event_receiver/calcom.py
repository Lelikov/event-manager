"""cal.com webhook translation into internal booking events.

Real cal.com webhooks carry top-level ``triggerEvent``/``createdAt``/``payload`` with
``organizer``, ``attendees[]``, ``responses.guests.value[]``, ``startTime``/``endTime`` and ``uid``,
signed with ``X-Cal-Signature-256`` (HMAC-SHA256 of the raw body). This module maps them onto the
canonical internal payload models (docs/audit/v2/CONTRACT_DECISIONS.md, D5).

Identity rules:
- ``uid`` is the booking identity (CloudEvent ``bookingid``).
- A reschedule mints a NEW ``uid``; the old one arrives as ``rescheduleUid`` and is carried
  as ``previous_booking_uid``.
"""

from dataclasses import dataclass
from typing import Any

from event_schemas.booking import BookingCancelledPayload, BookingRescheduledPayload
from event_schemas.types import EventType, SourceType
from pydantic import ValidationError

from event_receiver.errors import BadRequestError


CALCOM_SIGNATURE_HEADER = "x-cal-signature-256"
CALCOM_SOURCE = "calcom"


@dataclass(frozen=True)
class CalcomEvent:
    """Internal event derived from a cal.com webhook."""

    source: str
    event_type: EventType | str
    booking_uid: str | None
    data: dict[str, Any]


def transform_calcom_webhook(webhook: dict[str, Any]) -> CalcomEvent:
    """Translate a parsed cal.com webhook body into an internal event.

    Known triggers map to canonical booking.* payloads (source ``booking``); unknown triggers
    are preserved as ``calcom.<trigger>`` so the publisher parks them in events.unrouted.
    """
    trigger = webhook.get("triggerEvent")
    if not isinstance(trigger, str) or not trigger:
        raise BadRequestError("Missing triggerEvent in cal.com webhook")
    payload = webhook.get("payload")
    if not isinstance(payload, dict):
        raise BadRequestError("Missing payload object in cal.com webhook")

    builder = _TRIGGER_BUILDERS.get(trigger)
    if builder is None:
        uid = payload.get("uid")
        return CalcomEvent(
            source=CALCOM_SOURCE,
            event_type=f"calcom.{trigger.lower()}",
            booking_uid=uid if isinstance(uid, str) else None,
            data=payload,
        )
    return builder(payload)


def _build_created(payload: dict[str, Any]) -> CalcomEvent:
    data = {
        "users": _participants(payload),
        "start_time": _require(payload, "startTime"),
        "end_time": _require(payload, "endTime"),
    }
    return CalcomEvent(
        source=SourceType.BOOKING.value,
        event_type=EventType.BOOKING_CREATED,
        booking_uid=_require_uid(payload),
        data=data,
    )


def _build_cancelled(payload: dict[str, Any]) -> CalcomEvent:
    data = {
        "users": _participants(payload),
        "cancellation_reason": payload.get("cancellationReason"),
        "cancelled_by": payload.get("cancelledBy"),
    }
    _validate(BookingCancelledPayload, data)
    return CalcomEvent(
        source=SourceType.BOOKING.value,
        event_type=EventType.BOOKING_CANCELLED,
        booking_uid=_require_uid(payload),
        data=data,
    )


def _build_rescheduled(payload: dict[str, Any]) -> CalcomEvent:
    reschedule_uid = payload.get("rescheduleUid")
    data = {
        "users": _participants(payload),
        "start_time": _require(payload, "startTime"),
        "end_time": _require(payload, "endTime"),
        "previous_start_time": payload.get("rescheduleStartTime"),
        "previous_booking_uid": reschedule_uid if isinstance(reschedule_uid, str) else None,
        "rescheduled_by": payload.get("rescheduledBy"),
    }
    _validate(BookingRescheduledPayload, data)
    return CalcomEvent(
        source=SourceType.BOOKING.value,
        event_type=EventType.BOOKING_RESCHEDULED,
        booking_uid=_require_uid(payload),
        data=data,
    )


def _participants(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract organizer + all attendees + additional guests as a users list (guests -> role client)."""
    users: list[dict[str, Any]] = []
    seen_emails: set[str] = set()

    organizer = payload.get("organizer")
    if isinstance(organizer, dict) and isinstance(organizer.get("email"), str):
        users.append(_participant(organizer, role="organizer"))
        seen_emails.add(organizer["email"])

    attendees = payload.get("attendees")
    for attendee in attendees if isinstance(attendees, list) else []:
        if not isinstance(attendee, dict) or not isinstance(attendee.get("email"), str):
            continue
        if attendee["email"] in seen_emails:
            continue
        users.append(_participant(attendee, role="client"))
        seen_emails.add(attendee["email"])

    for guest_email in _guest_emails(payload):
        if guest_email in seen_emails:
            continue
        users.append({"email": guest_email, "role": "client"})
        seen_emails.add(guest_email)

    return users


def _participant(entry: dict[str, Any], *, role: str) -> dict[str, Any]:
    participant: dict[str, Any] = {"email": entry["email"], "role": role}
    time_zone = entry.get("timeZone")
    if isinstance(time_zone, str) and time_zone:
        participant["time_zone"] = time_zone
    return participant


def _guest_emails(payload: dict[str, Any]) -> list[str]:
    responses = payload.get("responses")
    if not isinstance(responses, dict):
        return []
    guests = responses.get("guests")
    if not isinstance(guests, dict):
        return []
    value = guests.get("value")
    if not isinstance(value, list):
        return []
    return [email for email in value if isinstance(email, str) and email]


def _require(payload: dict[str, Any], field: str) -> Any:
    value = payload.get(field)
    if value is None:
        raise BadRequestError(f"cal.com payload missing required field: {field!r}")
    return value


def _require_uid(payload: dict[str, Any]) -> str:
    uid = payload.get("uid")
    if not isinstance(uid, str) or not uid:
        raise BadRequestError("cal.com payload missing booking uid")
    return uid


def _validate(model: type, data: dict[str, Any]) -> None:
    try:
        model(**data)
    except ValidationError as exc:
        raise BadRequestError(f"Invalid cal.com booking payload: {exc}") from exc


_TRIGGER_BUILDERS = {
    "BOOKING_CREATED": _build_created,
    "BOOKING_CANCELLED": _build_cancelled,
    "BOOKING_RESCHEDULED": _build_rescheduled,
}
