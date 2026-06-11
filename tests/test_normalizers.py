from unittest.mock import MagicMock

from event_schemas.types import EventType

from event_receiver.normalizers import normalize_event_payload


class TestBookingCreatedNormalizer:
    def test_extracts_organizer_and_client(self) -> None:
        payload = {
            "volunteer_id": "550e8400-e29b-41d4-a716-446655440001",
            "client_id": "550e8400-e29b-41d4-a716-446655440002",
            "user": {"email": "org@test.com"},
            "client": {"email": "cli@test.com"},
            "start_time": "2026-01-01T10:00:00Z",
            "end_time": "2026-01-01T11:00:00Z",
        }
        result = normalize_event_payload(EventType.BOOKING_CREATED, payload)
        participants = result["normalized"]["participants"]
        assert len(participants) == 2
        assert {
            "email": "org@test.com",
            "role": "organizer",
            "user_id": "550e8400-e29b-41d4-a716-446655440001",
        } in participants
        assert {
            "email": "cli@test.com",
            "role": "client",
            "user_id": "550e8400-e29b-41d4-a716-446655440002",
        } in participants

    def test_extracts_without_user_ids(self) -> None:
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

    def test_extracts_all_participants_from_users_list(self) -> None:
        payload = {
            "volunteer_id": "550e8400-e29b-41d4-a716-446655440001",
            "client_id": "550e8400-e29b-41d4-a716-446655440002",
            "user": {"email": "org@test.com"},
            "client": {"email": "cli@test.com"},
            "start_time": "2026-01-01T10:00:00Z",
            "end_time": "2026-01-01T11:00:00Z",
            "users": [
                {"email": "org@test.com", "role": "organizer", "time_zone": "Europe/Madrid"},
                {"email": "cli@test.com", "role": "client"},
                {"email": "guest@test.com", "role": "guest"},
            ],
        }
        result = normalize_event_payload(EventType.BOOKING_CREATED, payload)
        participants = result["normalized"]["participants"]
        assert len(participants) == 3
        assert {
            "email": "org@test.com",
            "role": "organizer",
            "time_zone": "Europe/Madrid",
            "user_id": "550e8400-e29b-41d4-a716-446655440001",
        } in participants
        assert {
            "email": "cli@test.com",
            "role": "client",
            "user_id": "550e8400-e29b-41d4-a716-446655440002",
        } in participants
        assert {"email": "guest@test.com", "role": "client"} in participants

    def test_preserves_original_payload(self) -> None:
        payload = {
            "volunteer_id": "550e8400-e29b-41d4-a716-446655440001",
            "client_id": "550e8400-e29b-41d4-a716-446655440002",
            "user": {"email": "a@b.com"},
            "client": {"email": "c@d.com"},
            "start_time": "2026-01-01T10:00:00Z",
            "end_time": "2026-01-01T11:00:00Z",
        }
        result = normalize_event_payload(EventType.BOOKING_CREATED, payload)
        assert result["original"] is payload


class TestBookingReassignedNormalizer:
    def test_extracts_participants_from_users_list(self) -> None:
        payload = {
            "users": [
                {"email": "", "role": "previous_organizer"},
                {"email": "new@org.com", "role": "organizer"},
                {"email": "client@test.com", "role": "client"},
            ],
        }
        result = normalize_event_payload(EventType.BOOKING_REASSIGNED, payload)
        participants = result["normalized"]["participants"]
        assert len(participants) == 2
        assert {"email": "new@org.com", "role": "organizer"} in participants
        assert {"email": "client@test.com", "role": "client"} in participants


class TestBookingReminderSentNormalizer:
    def test_extracts_email(self) -> None:
        payload = {"client_id": "550e8400-e29b-41d4-a716-446655440002", "email": "user@test.com"}
        result = normalize_event_payload(EventType.BOOKING_REMINDER_SENT, payload)
        participants = result["normalized"]["participants"]
        assert len(participants) == 1
        assert participants[0]["email"] == "user@test.com"


class TestGetStreamNormalizer:
    def test_extracts_user_from_getstream_event(self) -> None:
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

    def test_client_role_for_non_owner(self) -> None:
        decoder = MagicMock(return_value="user@test.com")
        payload = {
            "type": "message.new",
            "user": {"id": "enc_id"},
            "members": [{"user_id": "enc_id", "role": "member"}],
        }
        result = normalize_event_payload(EventType.GETSTREAM_MESSAGE_NEW, payload, getstream_decoder=decoder)
        assert result["normalized"]["participants"][0]["role"] == "client"


class TestMalformedPayloads:
    def test_invalid_payload_returns_empty_participants(self) -> None:
        result = normalize_event_payload(EventType.BOOKING_CREATED, {"garbage": True})
        assert result["normalized"]["participants"] == []
        assert result["original"] == {"garbage": True}

    def test_unknown_event_type_returns_empty_participants(self) -> None:
        result = normalize_event_payload(EventType.BOOKING_RESCHEDULED, {"some": "data"})
        assert result["normalized"]["participants"] == []

    def test_empty_payload_returns_empty_participants(self) -> None:
        result = normalize_event_payload(EventType.BOOKING_REMINDER_SENT, {})
        assert result["normalized"]["participants"] == []


class TestNotificationCommandNormalizer:
    def test_extracts_participants_from_recipients(self) -> None:
        payload = {
            "booking_id": "b-1",
            "trigger_event": "BOOKING_CREATED",
            "recipients": [
                {"email": "org@example.com", "role": "organizer"},
                {"email": "cli@example.com", "role": "client"},
            ],
            "template_data": {"title": "Session"},
        }

        result = normalize_event_payload(EventType.NOTIFICATION_SEND_REQUESTED, payload)

        assert result["original"] == payload
        assert result["normalized"]["participants"] == [
            {"email": "org@example.com", "role": "organizer"},
            {"email": "cli@example.com", "role": "client"},
        ]

    def test_invalid_command_payload_yields_empty_participants(self) -> None:
        result = normalize_event_payload(EventType.NOTIFICATION_SEND_REQUESTED, {"recipients": "garbage"})

        assert result["normalized"]["participants"] == []


class TestBookingRejectedNormalizer:
    def test_extracts_client_participant(self) -> None:
        payload = {"client_email": "cli@example.com", "rejection_reasons": ["limit"]}

        result = normalize_event_payload(EventType.BOOKING_REJECTED, payload)

        assert result["normalized"]["participants"] == [{"email": "cli@example.com", "role": "client"}]


class TestMeetingUrlNormalizer:
    def test_extracts_recipient_from_canonical_payload(self) -> None:
        payload = {
            "email": "org@example.com",
            "recipient_role": "organizer",
            "meeting_url": "https://meet.example.com/x",
        }

        result = normalize_event_payload(EventType.MEETING_URL_CREATED, payload)

        assert result["normalized"]["participants"] == [{"email": "org@example.com", "role": "organizer"}]

    def test_legacy_users_list_shape_yields_empty_participants(self) -> None:
        payload = {"users": [{"email": "cli@example.com", "role": "client"}]}

        result = normalize_event_payload(EventType.MEETING_URL_DELETED, payload)

        assert result["normalized"]["participants"] == []


class TestUnknownEventType:
    def test_none_event_type_returns_envelope_with_empty_participants(self) -> None:
        payload = {"type": "member.added", "anything": 1}

        result = normalize_event_payload(None, payload)

        assert result == {"original": payload, "normalized": {"participants": []}}
