"""Tests for the cal.com ingress endpoint, validated against real webhook shapes (requests.jsonl)."""

import hashlib
import hmac
import json
from typing import Any

import pytest

from event_receiver.controllers.ingest import IngestController
from event_receiver.errors import BadRequestError, UnauthorizedError
from tests.test_ingest_controller import (
    CALCOM_WEBHOOK_SECRET,
    FakeJWTVerifier,
    FakePublisher,
    make_settings,
)


def calcom_body(trigger: str, payload: dict[str, Any], created_at: str = "2026-05-12T22:01:35.000Z") -> bytes:
    return json.dumps({"triggerEvent": trigger, "createdAt": created_at, "payload": payload}).encode()


def calcom_headers(body: bytes) -> dict[str, str]:
    signature = hmac.new(CALCOM_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return {"x-cal-signature-256": signature, "Content-Type": "application/json"}


def created_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "uid": "n3FHda8Cpy48QW4JZX9th7",
        "title": "qqqqqq Александр",
        "startTime": "2026-05-12T22:05:00Z",
        "endTime": "2026-05-12T22:10:00Z",
        "organizer": {
            "email": "lelikovas@gmail.com",
            "name": "Александр",
            "timeZone": "Europe/Madrid",
            "language": {"locale": "ru"},
        },
        "attendees": [
            {
                "email": "chied.bread@gmail.com",
                "name": "Александр",
                "timeZone": "Europe/Madrid",
                "utcOffset": 120,
            },
        ],
        "responses": {
            "email": {"label": "email_address", "value": "chied.bread@gmail.com"},
            "guests": {"label": "additional_guests", "value": []},
        },
        "status": "ACCEPTED",
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def publisher() -> FakePublisher:
    return FakePublisher()


@pytest.fixture
def controller(publisher: FakePublisher) -> IngestController:
    return IngestController(
        settings=make_settings(),
        publisher=publisher,
        authorization_jwt_verifier=FakeJWTVerifier(),
    )


class TestCalcomSignature:
    async def test_missing_signature_raises_unauthorized(self, controller, publisher) -> None:
        body = calcom_body("BOOKING_CREATED", created_payload())
        with pytest.raises(UnauthorizedError, match="Missing X-Cal-Signature-256"):
            await controller.ingest_calcom(headers={}, body=body)
        assert not publisher.published

    async def test_invalid_signature_raises_unauthorized(self, controller, publisher) -> None:
        body = calcom_body("BOOKING_CREATED", created_payload())
        with pytest.raises(UnauthorizedError, match=r"Invalid cal\.com"):
            await controller.ingest_calcom(headers={"x-cal-signature-256": "deadbeef"}, body=body)
        assert not publisher.published

    async def test_signed_non_json_body_raises_bad_request(self, controller) -> None:
        body = b"not json"
        with pytest.raises(BadRequestError, match="not valid JSON"):
            await controller.ingest_calcom(headers=calcom_headers(body), body=body)


class TestCalcomBookingCreated:
    async def test_happy_path_publishes_booking_created(self, controller, publisher) -> None:
        body = calcom_body("BOOKING_CREATED", created_payload())
        await controller.ingest_calcom(headers=calcom_headers(body), body=body)
        assert len(publisher.published) == 1
        published = publisher.published[0]
        assert str(published["event_type"]) in ("booking.created", "EventType.BOOKING_CREATED")
        assert published["source"] == "booking"
        assert published["booking_id"] == "n3FHda8Cpy48QW4JZX9th7"
        assert published["event_time"] == "2026-05-12T22:01:35.000Z"
        data = published["data"]
        assert data["user"] == {"email": "lelikovas@gmail.com", "time_zone": "Europe/Madrid"}
        assert data["client"] == {"email": "chied.bread@gmail.com", "time_zone": "Europe/Madrid"}
        assert data["start_time"] == "2026-05-12T22:05:00Z"
        assert data["end_time"] == "2026-05-12T22:10:00Z"

    async def test_guests_become_client_participants(self, controller, publisher) -> None:
        payload = created_payload()
        payload["responses"]["guests"]["value"] = ["guest@example.com"]
        body = calcom_body("BOOKING_CREATED", payload)
        await controller.ingest_calcom(headers=calcom_headers(body), body=body)
        users = publisher.published[0]["data"]["users"]
        assert {"email": "guest@example.com", "role": "client"} in users
        assert len(users) == 3

    async def test_missing_uid_raises_bad_request(self, controller) -> None:
        payload = created_payload()
        del payload["uid"]
        body = calcom_body("BOOKING_CREATED", payload)
        with pytest.raises(BadRequestError, match="uid"):
            await controller.ingest_calcom(headers=calcom_headers(body), body=body)

    async def test_missing_attendees_raises_bad_request(self, controller) -> None:
        body = calcom_body("BOOKING_CREATED", created_payload(attendees=[]))
        with pytest.raises(BadRequestError, match="client"):
            await controller.ingest_calcom(headers=calcom_headers(body), body=body)


class TestCalcomBookingCancelled:
    async def test_maps_cancellation_fields(self, controller, publisher) -> None:
        payload = created_payload(
            cancellationReason="Причина отмены",
            cancelledBy="lelikovas@gmail.com",
            status="CANCELLED",
        )
        body = calcom_body("BOOKING_CANCELLED", payload)
        await controller.ingest_calcom(headers=calcom_headers(body), body=body)
        published = publisher.published[0]
        assert str(published["event_type"]) in ("booking.cancelled", "EventType.BOOKING_CANCELLED")
        assert published["booking_id"] == "n3FHda8Cpy48QW4JZX9th7"
        data = published["data"]
        assert data["cancellation_reason"] == "Причина отмены"
        assert data["cancelled_by"] == "lelikovas@gmail.com"
        assert {"email": "lelikovas@gmail.com", "role": "organizer", "time_zone": "Europe/Madrid"} in data["users"]


class TestCalcomBookingRescheduled:
    async def test_new_uid_is_identity_and_old_uid_is_previous(self, controller, publisher) -> None:
        payload = created_payload(
            uid="bHm9EUG3QxL6CZWHBQQMxv",
            rescheduleUid="xaYwx8FnWLR2ZSvT8vg4WA",
            rescheduleStartTime="2026-05-12T22:15:00Z",
            rescheduleEndTime="2026-05-12T22:20:00Z",
            rescheduledBy="lelikovas@gmail.com",
            startTime="2026-05-12T22:40:51Z",
            endTime="2026-05-12T22:45:51Z",
        )
        body = calcom_body("BOOKING_RESCHEDULED", payload)
        await controller.ingest_calcom(headers=calcom_headers(body), body=body)
        published = publisher.published[0]
        assert published["booking_id"] == "bHm9EUG3QxL6CZWHBQQMxv"
        data = published["data"]
        assert data["previous_booking_uid"] == "xaYwx8FnWLR2ZSvT8vg4WA"
        assert data["previous_start_time"] == "2026-05-12T22:15:00Z"
        assert data["rescheduled_by"] == "lelikovas@gmail.com"
        assert data["start_time"] == "2026-05-12T22:40:51Z"


class TestCalcomUnknownTrigger:
    async def test_unknown_trigger_is_published_as_calcom_type(self, controller, publisher) -> None:
        payload = created_payload()
        body = calcom_body("BOOKING_PAYMENT_INITIATED", payload)
        await controller.ingest_calcom(headers=calcom_headers(body), body=body)
        published = publisher.published[0]
        assert published["event_type"] == "calcom.booking_payment_initiated"
        assert published["source"] == "calcom"
        assert published["booking_id"] == "n3FHda8Cpy48QW4JZX9th7"
        assert published["data"] == payload

    async def test_missing_trigger_event_raises_bad_request(self, controller) -> None:
        body = json.dumps({"payload": {}}).encode()
        with pytest.raises(BadRequestError, match="triggerEvent"):
            await controller.ingest_calcom(headers=calcom_headers(body), body=body)
