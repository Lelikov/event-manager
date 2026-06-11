"""Tests for Settings validation and normalization."""

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
