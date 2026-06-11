"""Tests for Settings validation, normalization and app wiring."""

import pytest
from pydantic import ValidationError

from event_receiver.main import RequestLoggerMiddleware, create_app
from tests.test_ingest_controller import make_settings


class TestLogLevelNormalization:
    def test_lowercase_value_is_normalized(self) -> None:
        settings = make_settings(log_level="info")
        assert settings.log_level == "INFO"

    def test_mixed_case_and_whitespace_normalized(self) -> None:
        settings = make_settings(log_level=" Debug ")
        assert settings.log_level == "DEBUG"

    def test_unknown_value_defaults_to_info(self) -> None:
        settings = make_settings(log_level="verbose")
        assert settings.log_level == "INFO"


class TestSecretStrengthValidation:
    def test_short_secret_rejected_outside_debug(self) -> None:
        with pytest.raises(ValidationError, match="booking_api_key"):
            make_settings(debug=False, booking_api_key="123")

    def test_placeholder_secret_rejected_outside_debug(self) -> None:
        with pytest.raises(ValidationError, match="admin_api_key"):
            make_settings(debug=False, admin_api_key="CHANGE_ME_PLEASE_NOW_REALLY")

    def test_all_same_character_key_rejected_outside_debug(self) -> None:
        with pytest.raises(ValidationError, match="getstream_user_id_encryption_key"):
            make_settings(debug=False, getstream_user_id_encryption_key="0" * 64)

    def test_strong_secrets_accepted_outside_debug(self) -> None:
        settings = make_settings(debug=False)
        assert settings.debug is False

    def test_weak_secret_tolerated_in_debug(self) -> None:
        settings = make_settings(debug=True, booking_api_key="123")
        assert settings.booking_api_key == "123"


class TestAppFactorySettingsGating:
    def test_cors_origins_come_from_settings(self) -> None:
        settings = make_settings(cors_origins="https://a.example, https://b.example")
        assert settings.cors_origins_list == ["https://a.example", "https://b.example"]
        application = create_app(settings)
        cors = next(m for m in application.user_middleware if m.cls.__name__ == "CORSMiddleware")
        assert cors.kwargs["allow_origins"] == ["https://a.example", "https://b.example"]

    def test_request_logger_middleware_enabled_only_in_debug(self) -> None:
        debug_app = create_app(make_settings(debug=True))
        prod_app = create_app(make_settings(debug=False))
        assert any(m.cls is RequestLoggerMiddleware for m in debug_app.user_middleware)
        assert not any(m.cls is RequestLoggerMiddleware for m in prod_app.user_middleware)
