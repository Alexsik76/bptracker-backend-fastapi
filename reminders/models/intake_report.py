from datetime import date, datetime
from typing import Any, ClassVar
from uuid import UUID

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, UniqueConstraint, Uuid, func, text
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
    # Plain uuid for now; FK to users is added by a migration when the auth module lands.
    user_id: UUID = Field(index=True)
    # Reference only: "what was taken" is read from `snapshot`, never joined via this FK.
    prescription_id: UUID | None = Field(
        default=None,
        sa_column=Column(
            Uuid, ForeignKey("prescriptions.id", ondelete="SET NULL"), nullable=True
        ),
    )
    confirmed_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), server_default=func.now()),
    )
    # Computed once at creation from reminder_config; never recomputed later.
    is_late: bool = Field(sa_column=Column(Boolean, nullable=False))
    # Self-contained copy of what was taken: [{medicine, amount, condition}, ...].
    snapshot: list[dict[str, Any]] = Field(sa_column=Column(JSONB, nullable=False))


class IntakeReportCreate(IntakeReportBase):
    # is_late / snapshot / confirmed_at are computed server-side, not client input.
    # prescription_id is optional and reference-only (see field docstring above).
    prescription_id: UUID | None = None


class IntakeReportRead(IntakeReportBase):
    id: UUID
    prescription_id: UUID | None
    confirmed_at: datetime
    is_late: bool
    snapshot: list[dict[str, Any]]
