"""Event payload normalizers.

Normalize event payloads from different sources into a standard structure
that event-saver can easily consume without complex extraction logic.
"""

import binascii
from typing import Any

from event_schemas import (
    BookingCancelledPayload,
    BookingCreatedPayload,
    BookingReassignedPayload,
    BookingReminderSentPayload,
    BookingRescheduledPayload,
    GetStreamEventPayload,
    JitsiEventPayload,
    NormalizedData,
    NormalizedPayload,
    UniSenderStatusPayload,
)
from event_schemas.types import EventType
from pydantic import ValidationError


def normalize_event_payload(
    event_type: EventType,
    payload: dict[str, Any],
    *,
    getstream_decoder: callable[[str], str] | None = None,
) -> NormalizedPayload:
    """Normalize event payload to standard structure.

    Args:
        event_type: Event type enum
        payload: Original event payload (dict)
        getstream_decoder: Optional decoder for GetStream encrypted user IDs

    Returns:
        NormalizedPayload with "original" and "normalized" keys.
        If normalization fails, returns empty normalized structure.

    """
    try:
        normalized = _normalize_by_type(event_type, payload, getstream_decoder=getstream_decoder)
    except ValidationError, KeyError, ValueError:
        # If normalization fails, return empty normalized structure
        normalized = {"participants": [], "booking": {}}

    return {"original": payload, "normalized": normalized}


def _normalize_by_type(
    event_type: EventType,
    payload: dict[str, Any],
    *,
    getstream_decoder: callable[[str], str] | None = None,
) -> NormalizedData:
    """Normalize payload based on event type using Pydantic validation."""
    match event_type:
        # Booking lifecycle events
        case EventType.BOOKING_CREATED:
            return _normalize_booking_created(payload)
        case EventType.BOOKING_RESCHEDULED:
            return _normalize_booking_rescheduled(payload)
        case EventType.BOOKING_REASSIGNED:
            return _normalize_booking_reassigned(payload)
        case EventType.BOOKING_CANCELLED:
            return _normalize_booking_cancelled(payload)
        case EventType.BOOKING_REMINDER_SENT:
            return _normalize_booking_reminder_sent(payload)

        # External integrations
        case EventType.UNISENDER_STATUS_CREATED:
            return _normalize_unisender_status(payload)

        # GetStream events
        case (
            EventType.GETSTREAM_MESSAGE_NEW
            | EventType.GETSTREAM_MESSAGE_UPDATED
            | EventType.GETSTREAM_MESSAGE_DELETED
            | EventType.GETSTREAM_MESSAGE_READ
        ):
            return _normalize_getstream_event(payload, getstream_decoder=getstream_decoder)

        # Jitsi events
        case EventType.JITSI_ROOM_CREATED | EventType.JITSI_PARTICIPANT_JOINED | EventType.JITSI_PARTICIPANT_LEFT:
            return _normalize_jitsi_event(payload)

        # Chat, Meeting, Notification events don't need normalization
        case _:
            return {"participants": [], "booking": {}}


def _normalize_booking_created(payload: dict[str, Any]) -> NormalizedData:
    """Normalize booking.created event."""
    validated = BookingCreatedPayload(**payload)

    return {
        "participants": [
            {
                "email": validated.user.email,
                "role": "organizer",
                "time_zone": validated.user.time_zone,
            },
            {
                "email": validated.client.email,
                "role": "client",
                "time_zone": validated.client.time_zone,
            },
        ],
        "booking": {
            "start_time": validated.start_time.isoformat(),
            "end_time": validated.end_time.isoformat(),
            "status": "created",
        },
    }


def _normalize_booking_rescheduled(payload: dict[str, Any]) -> NormalizedData:
    """Normalize booking.rescheduled event."""
    validated = BookingRescheduledPayload(**payload)

    return {
        "participants": [],
        "booking": {
            "start_time": validated.start_time.isoformat(),
            "end_time": validated.end_time.isoformat(),
        },
    }


def _normalize_booking_reassigned(payload: dict[str, Any]) -> NormalizedData:
    """Normalize booking.reassigned event."""
    validated = BookingReassignedPayload(**payload)

    return {
        "participants": [
            {
                "email": validated.user.email,
                "role": "organizer",
                "time_zone": validated.user.time_zone,
            },
        ],
        "booking": {},
    }


def _normalize_booking_cancelled(payload: dict[str, Any]) -> NormalizedData:
    """Normalize booking.cancelled event."""
    BookingCancelledPayload(**payload)

    return {
        "participants": [],
        "booking": {
            "status": "cancelled",
        },
    }


def _normalize_booking_reminder_sent(payload: dict[str, Any]) -> NormalizedData:
    """Normalize booking.reminder_sent event."""
    validated = BookingReminderSentPayload(**payload)

    return {
        "participants": [
            {
                "email": validated.email,
            },
        ],
        "booking": {},
    }


def _normalize_unisender_status(payload: dict[str, Any]) -> NormalizedData:
    """Normalize unisender.events.v1.transactional.status.create event."""
    validated = UniSenderStatusPayload(**payload)

    # Extract email from event_data
    email = validated.event_data.get("email")
    if not email or not isinstance(email, str):
        return {"participants": [], "booking": {}}

    return {
        "participants": [{"email": email}],
        "booking": {},
    }


def _normalize_getstream_event(
    payload: dict[str, Any],
    *,
    getstream_decoder: callable[[str], str],
) -> NormalizedData:
    """Normalize GetStream events (message.new, message.updated, etc.)."""
    validated = GetStreamEventPayload(**payload)

    # Extract user.id from GetStream event
    user_id = None
    if validated.user and isinstance(validated.user, dict):
        user_id = validated.user.get("id")

    if not user_id or not isinstance(user_id, str):
        return {"participants": [], "booking": {}}

    # Decode encrypted user_id to email if decoder is provided
    try:
        email = getstream_decoder(user_id)
    except (ValueError, UnicodeDecodeError, binascii.Error):
        # If decoding fails, skip this participant
        return {"participants": [], "booking": {}}

    return {
        "participants": [
            {
                "email": email,
            },
        ],
        "booking": {},
    }


def _normalize_jitsi_event(payload: dict[str, Any]) -> NormalizedData:
    """Normalize Jitsi events (room.created, participant.joined, etc.)."""
    JitsiEventPayload(**payload)

    # Extract email from context.user if available
    # JWT claims are already merged into payload by event-receiver
    context = payload.get("context")
    if not isinstance(context, dict):
        return {"participants": [], "booking": {}}

    user = context.get("user")
    if not isinstance(user, dict):
        return {"participants": [], "booking": {}}

    email = user.get("email")
    if not email or not isinstance(email, str):
        return {"participants": [], "booking": {}}

    role = user.get("role")

    return {
        "participants": [
            {
                "email": email,
                "role": role if isinstance(role, str) else None,
            },
        ],
        "booking": {},
    }
