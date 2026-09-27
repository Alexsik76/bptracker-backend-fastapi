import zoneinfo
from datetime import date, datetime
from typing import ClassVar
from uuid import UUID

from pydantic import field_validator, model_validator
from sqlalchemy import Column, DateTime, ForeignKey, Uuid
from sqlmodel import Field, SQLModel


class ExportRequest(SQLModel):
    tz: str
    date_from: date | None = None
    date_to: date | None = None

    @field_validator("tz")
    @classmethod
    def validate_timezone(cls, v: str) -> str:
        try:
            zoneinfo.ZoneInfo(v)
        except Exception as exc:
            raise ValueError(f"Invalid timezone identifier: '{v}'") from exc
        return v

    @model_validator(mode="after")
    def validate_date_range(self) -> "ExportRequest":
        if self.date_from is not None and self.date_to is not None:
            if self.date_from > self.date_to:
                raise ValueError("date_from cannot be after date_to")
        return self


class ExportResponse(SQLModel):
    message: str
    email: str


class ExportState(SQLModel, table=True):
    __tablename__: ClassVar[str] = "export_state"

    user_id: UUID = Field(
        sa_column=Column(
            Uuid,
            ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        )
    )
    last_export_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False)
    )
