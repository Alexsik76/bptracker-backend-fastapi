from datetime import datetime
from typing import ClassVar
from uuid import UUID

from sqlalchemy import Column, DateTime, Index, Integer, String, Text, Uuid, text
from sqlmodel import Field, SQLModel


class RequestEvent(SQLModel, table=True):
    __tablename__: ClassVar[str] = "request_events"

    id: UUID | None = Field(
        default=None,
        sa_column=Column(Uuid, primary_key=True, server_default=text("uuidv7()")),
    )
    occurred_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    user_id: UUID | None = Field(
        default=None,
        sa_column=Column(Uuid, nullable=True),
    )
    client: str = Field(sa_column=Column(String, nullable=False))
    module: str = Field(sa_column=Column(String, nullable=False))
    method: str = Field(sa_column=Column(String, nullable=False))
    route: str = Field(sa_column=Column(String, nullable=False))
    status_code: int = Field(sa_column=Column(Integer, nullable=False))
    duration_ms: int = Field(sa_column=Column(Integer, nullable=False))

    __table_args__ = (
        Index("ix_request_events_occurred_at", "occurred_at"),
        Index("ix_request_events_user_id_occurred_at", "user_id", "occurred_at"),
        Index("ix_request_events_module_occurred_at", "module", "occurred_at"),
    )


class ErrorEvent(SQLModel, table=True):
    __tablename__: ClassVar[str] = "error_events"

    id: UUID | None = Field(
        default=None,
        sa_column=Column(Uuid, primary_key=True, server_default=text("uuidv7()")),
    )
    occurred_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False, index=True),
    )
    user_id: UUID | None = Field(
        default=None,
        sa_column=Column(Uuid, nullable=True),
    )
    method: str = Field(sa_column=Column(String, nullable=False))
    route: str = Field(sa_column=Column(String, nullable=False))
    exception_type: str = Field(sa_column=Column(String, nullable=False))
    message: str = Field(sa_column=Column(String, nullable=False))
    traceback: str = Field(sa_column=Column(Text, nullable=False))
