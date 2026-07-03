from enum import StrEnum
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict
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
    access_token_expire_minutes: int = 60 * 24  # 1 day for now

    smtp_host: str
    smtp_port: int = 587
    smtp_username: str
    smtp_password: str
    smtp_from: str
    smtp_starttls: bool = True

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
