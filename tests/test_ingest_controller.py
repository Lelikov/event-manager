"""Tests for IngestController: auth, parsing, transformation and error mapping."""

import hashlib
import hmac
import json
from typing import Any

import pytest
import structlog.testing

from event_receiver.config import Settings
from event_receiver.controllers.ingest import IngestController
from event_receiver.errors import BadRequestError, UnauthorizedError


BOOKING_API_KEY = "booking-api-key-0123456789abcdef"
ADMIN_API_KEY = "admin-api-key-00123456789abcdef"
EMAIL_API_KEY = "email-api-key-00123456789abcdef"
GETSTREAM_API_SECRET = "getstream-secret-123456789abcdef"  # noqa: S105
CALCOM_WEBHOOK_SECRET = "calcom-webhook-secret-0123456789"  # noqa: S105


class FakePublisher:
    def __init__(self) -> None:
        self.published: list[dict[str, Any]] = []

    async def publish(self, **kwargs: Any) -> None:
        self.published.append(kwargs)


class FakeJWTVerifier:
    def __init__(self, claims: dict[str, Any] | None = None) -> None:
        self.claims = claims if claims is not None else {"room": "room-1"}

    def verify_signature(self, *, token: str) -> dict[str, Any]:
        if token == "invalid":  # noqa: S105
            raise UnauthorizedError("Invalid JWT signature")
        return dict(self.claims)

    def verify(self, *, claims: dict[str, Any], event_source: str, event_type: str) -> dict[str, Any]:
        del event_source, event_type
        excluded = {"source", "type", "exp", "iat", "nbf", "aud", "iss", "sub"}
        return {k: v for k, v in claims.items() if k not in excluded}


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "debug": True,
        "authorization_jwt_verify_key": "jwt-verify-key-0123456789abcdef",
        "authorization_jwt_issuer": "issuer",
        "authorization_jwt_audience": "audience",
        "email_api_key": EMAIL_API_KEY,
        "getstream_api_key": "getstream-key-000123456789abcdef",
        "getstream_api_secret": GETSTREAM_API_SECRET,
        "getstream_user_id_encryption_key": "getstream-encryption-key-0123456",
        "booking_api_key": BOOKING_API_KEY,
        "admin_api_key": ADMIN_API_KEY,
        "calcom_webhook_secret": CALCOM_WEBHOOK_SECRET,
        "event_users_api_url": "http://event-users.local",
        "event_users_api_token": "event-users-token-123456789abcde",
        "_env_file": None,
    }
    values.update(overrides)
    return Settings(**values)


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


def booking_cloudevent_headers(event_type: str = "booking.created") -> dict[str, str]:
    return {
        "Authorization": BOOKING_API_KEY,
        "ce-specversion": "1.0",
        "ce-id": "evt-1",
        "ce-source": "booking",
        "ce-type": event_type,
        "ce-time": "2026-05-12T22:01:35+00:00",
        "Content-Type": "application/json",
    }


def booking_created_body(**overrides: Any) -> bytes:
    payload: dict[str, Any] = {
        "booking_uid": "uid-1",
        "users": [
            {"role": "organizer", "email": "org@example.com"},
            {"role": "client", "email": "client@example.com"},
        ],
        "start_time": "2026-05-12T22:05:00+00:00",
        "end_time": "2026-05-12T22:10:00+00:00",
    }
    payload.update(overrides)
    return json.dumps(payload).encode()


def unisender_body(events: list[dict[str, Any]] | None = None) -> bytes:
    payload = {
        "auth": "PLACEHOLDER",
        "events_by_user": [
            {
                "events": events
                if events is not None
                else [
                    {
                        "event_name": "transactional_email_status",
                        "event_data": {
                            "email": "client@example.com",
                            "status": "delivered",
                            "metadata": {"booking_uid": "uid-1"},
                        },
                    },
                ],
            },
        ],
    }
    body_text = json.dumps(payload)
    with_key = body_text.replace('"auth": "PLACEHOLDER"', f'"auth": "{EMAIL_API_KEY}"')
    signature = hashlib.md5(with_key.encode()).hexdigest()  # noqa: S324
    return body_text.replace("PLACEHOLDER", signature).encode()


def getstream_headers(body: bytes) -> dict[str, str]:
    signature = hmac.new(GETSTREAM_API_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return {"X-SIGNATURE": signature}


class TestBodyNotLogged:
    async def test_booking_ingest_does_not_log_raw_body(self, controller, publisher) -> None:
        secret_marker = "client@example.com"  # noqa: S105
        with structlog.testing.capture_logs() as logs:
            await controller.ingest_booking(
                headers=booking_cloudevent_headers(),
                body=booking_created_body(),
            )
        assert publisher.published
        for entry in logs:
            assert secret_marker not in str(entry)

    async def test_unisender_ingest_does_not_log_auth_signature(self, controller, publisher) -> None:
        body = unisender_body()
        auth_signature = json.loads(body)["auth"]
        with structlog.testing.capture_logs() as logs:
            await controller.ingest_unisender_go(headers={}, body=body)
        assert publisher.published
        for entry in logs:
            assert auth_signature not in str(entry)


class TestBookingIngest:
    async def test_happy_path_publishes_transformed_payload(self, controller, publisher) -> None:
        await controller.ingest_booking(headers=booking_cloudevent_headers(), body=booking_created_body())
        assert len(publisher.published) == 1
        published = publisher.published[0]
        assert published["booking_id"] == "uid-1"
        assert published["event_type"] == "booking.created"
        assert published["data"]["user"]["email"] == "org@example.com"
        assert published["data"]["client"]["email"] == "client@example.com"

    async def test_invalid_api_key_raises_unauthorized(self, controller, publisher) -> None:
        headers = booking_cloudevent_headers() | {"Authorization": "wrong"}
        with pytest.raises(UnauthorizedError):
            await controller.ingest_booking(headers=headers, body=booking_created_body())
        assert not publisher.published

    async def test_missing_booking_uid_raises_bad_request(self, controller) -> None:
        body = json.dumps({"users": []}).encode()
        with pytest.raises(BadRequestError, match="booking_uid"):
            await controller.ingest_booking(headers=booking_cloudevent_headers(), body=body)

    async def test_missing_authorization_header_raises_unauthorized(self, controller, publisher) -> None:
        headers = booking_cloudevent_headers()
        del headers["Authorization"]
        with pytest.raises(UnauthorizedError):
            await controller.ingest_booking(headers=headers, body=booking_created_body())
        assert not publisher.published

    async def test_missing_email_raises_bad_request_not_keyerror(self, controller) -> None:
        body = booking_created_body(
            users=[{"role": "organizer"}, {"role": "client", "email": "client@example.com"}],
        )
        with pytest.raises(BadRequestError, match="missing required field"):
            await controller.ingest_booking(headers=booking_cloudevent_headers(), body=body)

    async def test_missing_start_time_raises_bad_request_not_keyerror(self, controller) -> None:
        payload = json.loads(booking_created_body())
        del payload["start_time"]
        with pytest.raises(BadRequestError, match="missing required field"):
            await controller.ingest_booking(headers=booking_cloudevent_headers(), body=json.dumps(payload).encode())

    async def test_non_dict_users_entries_raise_bad_request(self, controller) -> None:
        body = booking_created_body(users=["not-a-dict"])
        with pytest.raises(BadRequestError, match="users list"):
            await controller.ingest_booking(headers=booking_cloudevent_headers(), body=body)

    async def test_invalid_schema_raises_bad_request(self, controller) -> None:
        body = booking_created_body(start_time="not-a-datetime")
        with pytest.raises(BadRequestError, match="Invalid booking payload schema"):
            await controller.ingest_booking(headers=booking_cloudevent_headers(), body=body)


class TestJitsiIngest:
    def jitsi_headers(self) -> dict[str, str]:
        return {
            "Authorization": "Bearer some-jitsi-token",
            "ce-specversion": "1.0",
            "ce-id": "evt-jitsi-1",
            "ce-source": "jitsi",
            "ce-type": "jitsi.conference.joined",
            "Content-Type": "application/json",
        }

    async def test_happy_path_publishes_with_room_as_booking_id(self, controller, publisher) -> None:
        body = json.dumps({"context": {"user": {"email": "p@example.com", "role": "client"}}}).encode()
        await controller.ingest_jitsi(headers=self.jitsi_headers(), body=body)
        assert len(publisher.published) == 1
        assert publisher.published[0]["booking_id"] == "room-1"

    async def test_missing_authorization_raises_unauthorized(self, controller, publisher) -> None:
        headers = self.jitsi_headers()
        del headers["Authorization"]
        with pytest.raises(UnauthorizedError):
            await controller.ingest_jitsi(headers=headers, body=b"{}")
        assert not publisher.published

    async def test_non_object_data_raises_bad_request_not_typeerror(self, controller, publisher) -> None:
        body = json.dumps(["not", "an", "object"]).encode()
        with pytest.raises(BadRequestError, match="JSON object"):
            await controller.ingest_jitsi(headers=self.jitsi_headers(), body=body)
        assert not publisher.published


class TestUnisenderIngest:
    async def test_happy_path_publishes_each_event(self, controller, publisher) -> None:
        await controller.ingest_unisender_go(headers={}, body=unisender_body())
        assert len(publisher.published) == 1
        assert publisher.published[0]["booking_id"] == "uid-1"
        assert publisher.published[0]["source"] == "unisender-go"

    async def test_invalid_signature_raises_unauthorized(self, controller, publisher) -> None:
        body = unisender_body().replace(b'"auth": "', b'"auth": "0')
        with pytest.raises(UnauthorizedError):
            await controller.ingest_unisender_go(headers={}, body=body)
        assert not publisher.published

    async def test_empty_body_raises_bad_request(self, controller) -> None:
        with pytest.raises(BadRequestError, match="Empty request body"):
            await controller.ingest_unisender_go(headers={}, body=b"")

    async def test_signed_non_json_body_raises_bad_request_not_valueerror(self, controller) -> None:
        body_text = '"auth": "PLACEHOLDER" this is not json'
        with_key = body_text.replace("PLACEHOLDER", EMAIL_API_KEY)
        signature = hashlib.md5(with_key.encode()).hexdigest()  # noqa: S324
        body = body_text.replace("PLACEHOLDER", signature).encode()
        with pytest.raises(BadRequestError, match="not valid JSON"):
            await controller.ingest_unisender_go(headers={}, body=body)


class TestGetstreamIngest:
    async def test_happy_path_publishes_with_channel_id_as_booking_id(self, controller, publisher) -> None:
        body = json.dumps({"type": "message.new", "channel_id": "uid-9"}).encode()
        await controller.ingest_getstream(headers=getstream_headers(body), body=body)
        assert len(publisher.published) == 1
        assert publisher.published[0]["event_type"] == "getstream.message.new"
        assert publisher.published[0]["booking_id"] == "uid-9"

    async def test_missing_signature_raises_unauthorized(self, controller, publisher) -> None:
        with pytest.raises(UnauthorizedError, match="Missing X-SIGNATURE"):
            await controller.ingest_getstream(headers={}, body=b"{}")
        assert not publisher.published

    async def test_invalid_signature_raises_unauthorized(self, controller, publisher) -> None:
        with pytest.raises(UnauthorizedError, match="Invalid Getstream"):
            await controller.ingest_getstream(headers={"X-SIGNATURE": "deadbeef"}, body=b"{}")
        assert not publisher.published

    async def test_non_json_body_raises_bad_request_not_valueerror(self, controller) -> None:
        body = b"not json"
        with pytest.raises(BadRequestError, match="not valid JSON"):
            await controller.ingest_getstream(headers=getstream_headers(body), body=body)


class TestAdminIngest:
    def admin_headers(self, event_type: str = "user.email.change_requested") -> dict[str, str]:
        return {
            "Authorization": ADMIN_API_KEY,
            "ce-specversion": "1.0",
            "ce-id": "evt-admin-1",
            "ce-source": "admin",
            "ce-type": event_type,
            "Content-Type": "application/json",
        }

    async def test_happy_path_publishes(self, controller, publisher) -> None:
        body = json.dumps({"booking_uid": "uid-2", "old_email": "a@b.c", "new_email": "d@e.f"}).encode()
        await controller.ingest_admin(headers=self.admin_headers(), body=body)
        assert len(publisher.published) == 1
        assert publisher.published[0]["booking_id"] == "uid-2"

    async def test_invalid_api_key_raises_unauthorized(self, controller, publisher) -> None:
        headers = self.admin_headers() | {"Authorization": "wrong"}
        with pytest.raises(UnauthorizedError):
            await controller.ingest_admin(headers=headers, body=b"{}")
        assert not publisher.published

    async def test_missing_authorization_header_raises_unauthorized(self, controller, publisher) -> None:
        headers = self.admin_headers()
        del headers["Authorization"]
        with pytest.raises(UnauthorizedError):
            await controller.ingest_admin(headers=headers, body=b"{}")
        assert not publisher.published
