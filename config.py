from enum import StrEnum
from functools import lru_cache
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy import URL


class Environment(StrEnum):
    DEV = "dev"
    PROD = "prod"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Selects environment-specific behavior (see is_dev). Read from ENVIRONMENT.
    environment: Environment = Environment.DEV

    # Connection parts are the single source of truth; the full URL is derived, not stored.
    postgres_user: str
    postgres_password: str
    postgres_db: str
    postgres_host: str = "localhost"
    postgres_port: int = 5432

    # No default: a missing JWT_SECRET must fail loudly, never fall back silently.
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_ttl_days: int = 30

    smtp_host: str
    smtp_port: int = 587
    smtp_username: str
    smtp_password: str
    smtp_from: str
    smtp_starttls: bool = True
    smtp_timeout: int = 10

    magic_link_base_url: str
    magic_link_ttl_minutes: int = 15
    magic_link_token_bytes: int = 32

    email_outbox_poll_seconds: int = 30
    email_outbox_batch_size: int = 50
    email_outbox_max_attempts: int = 10
    email_outbox_worker_enabled: bool = True
    email_outbox_lease_seconds: int = 300

    cleanup_interval_minutes: int = 60
    cleanup_worker_enabled: bool = True

    gemini_api_key: str
    gemini_model: str = "gemini-2.0-flash"
    gemini_timeout_seconds: int = 30
    analyze_max_file_bytes: int = 10 * 1024 * 1024

    export_cooldown_minutes: int = 10
    export_sheets_template_url: str

    webauthn_rp_id: str
    webauthn_rp_name: str = "BP Tracker"
    # Allowed origins for WebAuthn (web frontend origin + Android apk-key-hash origins)
    webauthn_origins: Annotated[list[str], NoDecode]
    webauthn_challenge_ttl_minutes: int = 5

    @field_validator("webauthn_origins", mode="before")
    @classmethod
    def parse_webauthn_origins(cls, v: any) -> any:
        if isinstance(v, str):
            if not v.strip():
                raise ValueError("webauthn_origins cannot be empty")
            parsed = [item.strip() for item in v.split(",")]
            parsed = [item for item in parsed if item]
            if not parsed:
                raise ValueError("webauthn_origins cannot be empty")
            return parsed
        if isinstance(v, list):
            if not v:
                raise ValueError("webauthn_origins cannot be empty")
            return v
        return v

    @property
    def is_dev(self) -> bool:
        return self.environment is Environment.DEV

    @property
    def database_url(self) -> URL:
        # Built via SQLAlchemy so special characters in the password are escaped
        # correctly — safer than hand-concatenating a URL string.
        return URL.create(
            drivername="postgresql+psycopg",
            username=self.postgres_user,
            password=self.postgres_password,
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
