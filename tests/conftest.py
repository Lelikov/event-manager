"""Test environment: provide required Settings env vars before application imports."""

import os


_TEST_ENV_DEFAULTS = {
    "DEBUG": "true",
    "AUTHORIZATION_JWT_VERIFY_KEY": "jwt-verify-key-0123456789abcdef",
    "AUTHORIZATION_JWT_ISSUER": "issuer",
    "AUTHORIZATION_JWT_AUDIENCE": "audience",
    "EMAIL_API_KEY": "email-api-key-00123456789abcdef",
    "GETSTREAM_API_KEY": "getstream-key-000123456789abcdef",
    "GETSTREAM_API_SECRET": "getstream-secret-123456789abcdef",
    "GETSTREAM_USER_ID_ENCRYPTION_KEY": "getstream-encryption-key-0123456",
    "BOOKING_API_KEY": "booking-api-key-0123456789abcdef",
    "ADMIN_API_KEY": "admin-api-key-00123456789abcdef",
    "CALCOM_WEBHOOK_SECRET": "calcom-webhook-secret-0123456789",
    "EVENT_USERS_API_URL": "http://event-users.local",
    "EVENT_USERS_API_TOKEN": "event-users-token-123456789abcde",
}

for _key, _value in _TEST_ENV_DEFAULTS.items():
    os.environ.setdefault(_key, _value)
