from datetime import datetime
from typing import Annotated, ClassVar
from uuid import UUID

from pydantic import AfterValidator, EmailStr
from sqlalchemy import Column, DateTime, String, Uuid, func, text
from sqlmodel import Field, SQLModel


def lowercase_email(v: str) -> str:
    # RFC 5321 technically allows case-sensitive local parts, but practically
    # all major email providers treat emails as case-insensitive.
    return v.lower()


NormalizedEmail = Annotated[EmailStr, AfterValidator(lowercase_email)]


class UserBase(SQLModel):
    email: EmailStr = Field(index=True, sa_column_kwargs={"unique": True})
    # IANA timezone (e.g. "Europe/Kyiv"); per domain spec, not used by auth logic yet.
    timezone: str | None = None


class User(UserBase, table=True):
    __tablename__: ClassVar[str] = "users"

    id: UUID | None = Field(
        default=None,
        sa_column=Column(Uuid, primary_key=True, server_default=text("uuidv7()")),
    )
    # Nullable: a future passkey-only user has no password.
    password_hash: str | None = None
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), server_default=func.now()),
    )


class UserCreate(SQLModel):
    email: NormalizedEmail
    # Plaintext input, never persisted as-is — hashed in auth/crud.py before storage.
    password: str = Field(min_length=8)
    timezone: str | None = None


class UserRead(SQLModel):
    id: UUID
    email: EmailStr
    timezone: str | None
    created_at: datetime


class TokenResponse(SQLModel):
    access_token: str
    token_type: str = "bearer"


class MagicLink(SQLModel, table=True):
    __tablename__: ClassVar[str] = "magic_links"

    email: str = Field(sa_column=Column(String, primary_key=True))
    token_hash: str = Field(sa_column=Column(String, unique=True, index=True, nullable=False))
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), server_default=func.now()),
    )
    expires_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
