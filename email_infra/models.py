from datetime import datetime
from enum import StrEnum
from typing import ClassVar
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import Column, DateTime, Index, Integer, String, Uuid, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class EmailStatus(StrEnum):
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"
    DEAD = "DEAD"


class EmailOutbox(SQLModel, table=True):
    __tablename__: ClassVar[str] = "email_outbox"

    id: UUID | None = Field(
        default=None,
        sa_column=Column(Uuid, primary_key=True, server_default=text("uuidv7()")),
    )
    to: str = Field(sa_column=Column(String, nullable=False))
    subject: str = Field(sa_column=Column(String, nullable=False))
    body: str = Field(sa_column=Column(String, nullable=False))
    attachments: list[dict] | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    status: EmailStatus = Field(
        sa_column=Column(sa.Enum(EmailStatus, name="emailstatus"), nullable=False)
    )
    attempts: int = Field(default=0, sa_column=Column(Integer, nullable=False))
    last_error: str | None = Field(default=None, sa_column=Column(String, nullable=True))
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), server_default=func.now()),
    )
    next_attempt_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    user_id: UUID | None = Field(
        default=None,
        sa_column=Column(Uuid, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True),
    )

    __table_args__ = (Index("ix_email_outbox_status_next_attempt_at", "status", "next_attempt_at"),)
