from datetime import datetime
from typing import ClassVar
from uuid import UUID

from sqlalchemy import Column, DateTime, ForeignKey, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from prescriptions.models.enums import CourseType, DoseUnit, FreqPeriodUnit, WhenSlot


class MedicationItemBase(SQLModel):
    medicine: str
    condition: str | None = None

    # when_slots stored as jsonb array of WhenSlot values.
    when_slots: list[WhenSlot] = Field(sa_column=Column(JSONB, nullable=False))

    # --- dose axis ---
    dose_amount: str
    dose_unit: DoseUnit | None = None

    # --- frequency axis ---
    freq_count: int
    freq_period: int
    freq_period_unit: FreqPeriodUnit

    # --- course axis ---
    course_type: CourseType = CourseType.ONGOING
    course_start: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    course_intakes: int | None = None


class MedicationItem(MedicationItemBase, table=True):
    __tablename__: ClassVar[str] = "medication_items"

    id: UUID | None = Field(
        default=None,
        sa_column=Column(Uuid, primary_key=True, server_default=text("uuidv7()")),
    )
    # Composition: deleting a prescription cascades to its medication items.
    prescription_id: UUID = Field(
        sa_column=Column(Uuid, ForeignKey("prescriptions.id", ondelete="CASCADE"), nullable=False)
    )


class MedicationItemCreate(MedicationItemBase):
    pass


class MedicationItemRead(MedicationItemBase):
    id: UUID
    prescription_id: UUID


class MedicationItemUpdate(SQLModel):
    medicine: str | None = None
    condition: str | None = None
    when_slots: list[WhenSlot] | None = None
    dose_amount: str | None = None
    dose_unit: DoseUnit | None = None
    freq_count: int | None = None
    freq_period: int | None = None
    freq_period_unit: FreqPeriodUnit | None = None
    course_type: CourseType | None = None
    course_start: datetime | None = None
    course_intakes: int | None = None
