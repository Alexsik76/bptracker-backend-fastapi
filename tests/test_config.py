import pytest
from pydantic import ValidationError

from config import Settings


def test_webauthn_origins_parsing():
    def make_settings(**kwargs):
        defaults = {
            "postgres_user": "dummy",
            "postgres_password": "dummy",
            "postgres_db": "dummy",
            "jwt_secret": "dummy",
            "smtp_host": "smtp.example.com",
            "smtp_username": "user",
            "smtp_password": "pass",
            "smtp_from": "noreply@example.com",
            "magic_link_base_url": "http://localhost",
            "export_sheets_template_url": "http://localhost",
            "webauthn_rp_id": "localhost",
            "gemini_api_key": "dummy_key",
            "allowed_emails": "test@example.com",
        }
        return Settings(**{**defaults, **kwargs})

    # A comma-separated string produces the expected list, with whitespace stripped
    s1 = make_settings(webauthn_origins="http://localhost:5173, android:apk-key-hash:AbCd")
    assert s1.webauthn_origins == ["http://localhost:5173", "android:apk-key-hash:AbCd"]

    # A single value with no comma produces a one-element list
    s2 = make_settings(webauthn_origins="http://localhost:5173")
    assert s2.webauthn_origins == ["http://localhost:5173"]

    # A list passes through unchanged
    s3 = make_settings(webauthn_origins=["http://localhost:5173", "android:apk-key-hash:XYZ"])
    assert s3.webauthn_origins == ["http://localhost:5173", "android:apk-key-hash:XYZ"]

    # An empty or whitespace-only value raises a validation error
    with pytest.raises(ValidationError) as exc_info:
        make_settings(webauthn_origins="")
    assert "webauthn_origins" in str(exc_info.value)

    with pytest.raises(ValidationError) as exc_info:
        make_settings(webauthn_origins="   ")
    assert "webauthn_origins" in str(exc_info.value)

    # An empty list raises a validation error
    with pytest.raises(ValidationError) as exc_info:
        make_settings(webauthn_origins=[])
    assert "webauthn_origins" in str(exc_info.value)


def test_allowed_emails_parsing():
    def make_settings(**kwargs):
        defaults = {
            "postgres_user": "dummy",
            "postgres_password": "dummy",
            "postgres_db": "dummy",
            "jwt_secret": "dummy",
            "smtp_host": "smtp.example.com",
            "smtp_username": "user",
            "smtp_password": "pass",
            "smtp_from": "noreply@example.com",
            "magic_link_base_url": "http://localhost",
            "export_sheets_template_url": "http://localhost",
            "webauthn_rp_id": "localhost",
            "gemini_api_key": "dummy_key",
            "webauthn_origins": "http://localhost:5173",
        }
        return Settings(**{**defaults, **kwargs})

    # Comma-separated list with whitespace stripped and lowercased
    s1 = make_settings(allowed_emails="A@Example.com, b@example.com ")
    assert s1.allowed_emails == ["a@example.com", "b@example.com"]

    # Single value
    s2 = make_settings(allowed_emails="A@Example.com")
    assert s2.allowed_emails == ["a@example.com"]

    # List input passes and is lowercased
    s3 = make_settings(allowed_emails=["Mixed@Example.Com"])
    assert s3.allowed_emails == ["mixed@example.com"]

    # Empty raises validation error
    with pytest.raises(ValidationError) as exc_info:
        make_settings(allowed_emails="")
    assert "allowed_emails" in str(exc_info.value)

    # Empty list raises validation error
    with pytest.raises(ValidationError) as exc_info:
        make_settings(allowed_emails=[])
    assert "allowed_emails" in str(exc_info.value)
