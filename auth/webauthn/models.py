from datetime import datetime
from enum import StrEnum
from typing import ClassVar
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    LargeBinary,
    String,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class ChallengePurpose(StrEnum):
    REGISTRATION = "registration"
    AUTHENTICATION = "authentication"


class WebAuthnCredential(SQLModel, table=True):
    __tablename__: ClassVar[str] = "webauthn_credentials"

    id: UUID | None = Field(
        default=None,
        sa_column=Column(Uuid, primary_key=True, server_default=text("uuidv7()")),
    )
    user_id: UUID = Field(
        sa_column=Column(
            Uuid, sa.ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
        )
    )
    credential_id: bytes = Field(
        sa_column=Column(LargeBinary, unique=True, index=True, nullable=False)
    )
    public_key: bytes = Field(sa_column=Column(LargeBinary, nullable=False))
    sign_count: int = Field(default=0, sa_column=Column(BigInteger, nullable=False))
    transports: list[str] | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    backup_eligible: bool = Field(sa_column=Column(Boolean, nullable=False))
    backup_state: bool = Field(sa_column=Column(Boolean, nullable=False))
    label: str | None = Field(default=None, sa_column=Column(String, nullable=True))
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), server_default=func.now()),
    )
    last_used_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )


class WebAuthnChallenge(SQLModel, table=True):
    __tablename__: ClassVar[str] = "webauthn_challenges"

    challenge: bytes = Field(sa_column=Column(LargeBinary, primary_key=True))
    user_id: UUID | None = Field(default=None, sa_column=Column(Uuid, nullable=True))
    purpose: ChallengePurpose = Field(
        sa_column=Column(sa.Enum(ChallengePurpose, name="challengepurpose"), nullable=False)
    )
    expires_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), server_default=func.now()),
    )


class WebAuthnCredentialRead(SQLModel):
    id: UUID
    label: str | None
    transports: list[str] | None
    created_at: datetime
    last_used_at: datetime | None
