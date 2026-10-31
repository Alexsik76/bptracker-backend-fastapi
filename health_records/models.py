from datetime import datetime
from typing import Any, ClassVar, Self
from uuid import UUID

from pydantic import model_validator
from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class HealthRecordBase(SQLModel):
    record_type: str = Field(sa_column=Column(String, nullable=False))
    values: dict[str, Any] = Field(sa_column=Column(JSONB, nullable=False))
    start_time: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    end_time: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    data_origin: str = Field(sa_column=Column(String, nullable=False))
    hc_record_id: str = Field(sa_column=Column(String, nullable=False))


class HealthRecord(HealthRecordBase, table=True):
    __tablename__: ClassVar[str] = "health_records"
    __table_args__ = (
        UniqueConstraint("user_id", "hc_record_id", name="uq_health_records_user_hc_record"),
        Index(
            "ix_health_records_user_id_record_type_start_time",
            "user_id",
            "record_type",
            "start_time",
        ),
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
    updated_at: datetime | None = Field(
        default=None,
        sa_column=Column(
            DateTime(timezone=True),
            server_default=func.now(),
            onupdate=func.now(),
            nullable=False,
        ),
    )


class HealthRecordUpsert(HealthRecordBase):
    pass


class HealthRecordRead(HealthRecordBase):
    id: UUID
    updated_at: datetime


class HealthRecordBatchRequest(SQLModel):
    upserts: list[HealthRecordUpsert] = Field(default_factory=list)
    deleted_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_batch_size(self) -> Self:
        if len(self.upserts) + len(self.deleted_ids) > 1000:
            raise ValueError("Combined size of upserts and deleted_ids cannot exceed 1000 items")
        return self


class HealthRecordBatchResponse(SQLModel):
    created: int
    updated: int
    deleted: int


class HealthRecordListResponse(SQLModel):
    """Paged envelope for GET /health-records."""

    items: list[HealthRecordRead]
    total: int
    limit: int
    offset: int
