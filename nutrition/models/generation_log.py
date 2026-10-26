from datetime import date as dt_date
from datetime import datetime
from typing import ClassVar
from uuid import UUID

from sqlalchemy import Column, Date, DateTime, ForeignKey, Integer, String, Text, Uuid, func, text
from sqlmodel import Field, SQLModel


class GenerationLog(SQLModel, table=True):
    __tablename__: ClassVar[str] = "generation_log"

    id: UUID | None = Field(
        default=None,
        sa_column=Column(
            Uuid,
            primary_key=True,
            server_default=text("uuidv7()"),
        ),
    )
    user_id: UUID = Field(
        sa_column=Column(
            Uuid,
            ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    date: dt_date = Field(sa_column=Column(Date, nullable=False, index=True))
    scope: str = Field(sa_column=Column(String, nullable=False))
    model: str = Field(sa_column=Column(String, nullable=False))
    prompt_version: str = Field(sa_column=Column(String, nullable=False))
    prompt: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    duration_ms: int | None = Field(default=None, sa_column=Column(Integer, nullable=True))
    input_tokens: int | None = Field(default=None, sa_column=Column(Integer, nullable=True))
    output_tokens: int | None = Field(default=None, sa_column=Column(Integer, nullable=True))
    raw_response: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(
            DateTime(timezone=True),
            server_default=func.now(),
            nullable=False,
        ),
    )
