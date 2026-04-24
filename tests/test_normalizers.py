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
