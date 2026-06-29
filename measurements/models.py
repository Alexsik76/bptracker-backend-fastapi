from datetime import datetime
from uuid import UUID

from sqlalchemy import Column, DateTime, Uuid, func, text
from sqlmodel import Field, SQLModel


class MeasurementBase(SQLModel):
    # App-level range validation (Pydantic). DB CHECK constraints deferred.
    sys: int = Field(ge=40, le=300)
    dia: int = Field(ge=20, le=200)
    pulse: int = Field(ge=30, le=250)


class Measurement(MeasurementBase, table=True):
    __tablename__ = "measurements"

    id: UUID | None = Field(
        default=None,
        sa_column=Column(Uuid, primary_key=True, server_default=text("uuidv7()")),
    )
    recorded_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), server_default=func.now()),
    )
    # Plain uuid for now; FK to users is added by a migration when the auth module lands.
    user_id: UUID = Field(index=True)


class MeasurementCreate(MeasurementBase):
    # Client may supply a timestamp; if omitted, the DB default (now) applies.
    recorded_at: datetime | None = None


class MeasurementRead(MeasurementBase):
    id: UUID
    recorded_at: datetime
