from datetime import datetime
from typing import Annotated, ClassVar
from uuid import UUID

from pydantic import AfterValidator, EmailStr
from sqlalchemy import Column, DateTime, ForeignKey, String, Uuid, func, text
from sqlmodel import Field, SQLModel


def lowercase_email(v: str) -> str:
    # RFC 5321 technically allows case-sensitive local parts, but practically
    # all major email providers treat emails as case-insensitive.
    return v.lower()


NormalizedEmail = Annotated[EmailStr, AfterValidator(lowercase_email)]


class UserBase(SQLModel):
    email: EmailStr = Field(index=True, sa_column_kwargs={"unique": True})


class User(UserBase, table=True):
    __tablename__: ClassVar[str] = "users"

    id: UUID | None = Field(
        default=None,
        sa_column=Column(Uuid, primary_key=True, server_default=text("uuidv7()")),
    )
    display_name: str | None = Field(
        default=None,
        sa_column=Column(String(120), nullable=True),
    )
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), server_default=func.now()),
    )
    last_export_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )


class UserRead(SQLModel):
    id: UUID
    email: EmailStr
    display_name: str | None = None
    created_at: datetime


class TokenResponse(SQLModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class MagicLink(SQLModel, table=True):
    __tablename__: ClassVar[str] = "magic_links"

    email: str = Field(sa_column=Column(String, primary_key=True))
    token_hash: str = Field(sa_column=Column(String, unique=True, index=True, nullable=False))
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), server_default=func.now()),
    )
    expires_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))


class Session(SQLModel, table=True):
    __tablename__: ClassVar[str] = "sessions"

    id: UUID = Field(
        default=None,
        sa_column=Column(Uuid, primary_key=True, server_default=text("uuidv7()")),
    )
    user_id: UUID = Field(
        sa_column=Column(
            Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
        )
    )
    token_hash: str = Field(sa_column=Column(String, unique=True, index=True, nullable=False))
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), server_default=func.now()),
    )
    expires_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    last_used_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    revoked_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    user_agent: str | None = Field(
        default=None,
        sa_column=Column(String(255), nullable=True),
    )


class SessionRead(SQLModel):
    id: UUID
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime
    user_agent: str | None
