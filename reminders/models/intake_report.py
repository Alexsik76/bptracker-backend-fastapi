from datetime import date, datetime
from typing import Any, ClassVar
from uuid import UUID

from sqlalchemy import Column, DateTime, ForeignKey, UniqueConstraint, Uuid, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from prescriptions.models import WhenSlot


class IntakeReportBase(SQLModel):
    period: WhenSlot
    # Calendar date this intake belongs to (client's timezone), not necessarily today.
    date: date


class IntakeReport(IntakeReportBase, table=True):
    __tablename__: ClassVar[str] = "intake_reports"
    __table_args__ = (
        UniqueConstraint("user_id", "period", "date", name="uq_intake_reports_user_period_date"),
    )

    id: UUID | None = Field(
        default=None,
        sa_column=Column(Uuid, primary_key=True, server_default=text("uuidv7()")),
    )
    user_id: UUID = Field(
        sa_column=Column(
            Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
        )
    )
    # When the medication was actually taken (the real-world event). Client-supplied
    # per docs/conventions.md — the backend stores it as received, never interprets it.
    taken_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    # When this row was last written (created OR edited). Always server-set to
    # now() on every write, never client-supplied.
    recorded_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), server_default=func.now()),
    )
    # Self-contained copy of what was taken: [{medicine, amount, condition}, ...].
    snapshot: list[dict[str, Any]] = Field(sa_column=Column(JSONB, nullable=False))


class IntakeReportCreate(IntakeReportBase):
    # Optional: absent means "just taken now" (server sets taken_at = now()).
    # Present means client is recording a moment other than now (see docs/conventions.md).
    taken_at: datetime | None = None


class IntakeReportRead(IntakeReportBase):
    id: UUID
    taken_at: datetime
    recorded_at: datetime
    snapshot: list[dict[str, Any]]
