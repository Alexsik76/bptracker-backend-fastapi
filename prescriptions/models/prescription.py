from datetime import date, datetime
from typing import ClassVar
from uuid import UUID

from sqlalchemy import Column, DateTime, ForeignKey, Uuid, func, text
from sqlmodel import Field, SQLModel


class PrescriptionBase(SQLModel):
    doctor: str
    prescribed_on: date
    # Multiple active prescriptions per user are allowed (multi-doctor scenario) —
    # no "single active" invariant, this is a plain flag.
    is_active: bool = True


class Prescription(PrescriptionBase, table=True):
    __tablename__: ClassVar[str] = "prescriptions"

    id: UUID | None = Field(
        default=None,
        sa_column=Column(Uuid, primary_key=True, server_default=text("uuidv7()")),
    )
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), server_default=func.now()),
    )
    user_id: UUID = Field(
        sa_column=Column(Uuid, ForeignKey("users.id"), index=True, nullable=False)
    )


class PrescriptionCreate(PrescriptionBase):
    pass


class PrescriptionRead(PrescriptionBase):
    id: UUID
    created_at: datetime


class PrescriptionUpdate(SQLModel):
    doctor: str | None = None
    prescribed_on: date | None = None
    is_active: bool | None = None
