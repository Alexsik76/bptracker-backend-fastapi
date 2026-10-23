from datetime import date as dt_date
from datetime import datetime
from typing import ClassVar
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import Column, Date, DateTime, ForeignKey, Integer, String, Text, Uuid, func, text
from sqlmodel import Field, SQLModel

from nutrition.models.enums import MenuNoteKind


class MenuNoteBase(SQLModel):
    date: dt_date = Field(sa_column=Column(Date, nullable=False, index=True))
    kind: MenuNoteKind = Field(
        sa_column=Column(
            sa.Enum(
                MenuNoteKind,
                name="menunotekind",
                values_callable=lambda x: [e.value for e in x],
            ),
            nullable=False,
        )
    )
    text: str = Field(sa_column=Column(Text, nullable=False))
    answer: str | None = Field(default=None, sa_column=Column(Text, nullable=True))


class MenuNote(MenuNoteBase, table=True):
    __tablename__: ClassVar[str] = "menu_notes"

    id: UUID | None = Field(
        default=None,
        sa_column=Column(Uuid, primary_key=True, server_default=text("uuidv7()")),
    )
    user_id: UUID = Field(
        sa_column=Column(
            Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
        )
    )
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), server_default=func.now(), nullable=False),
    )


class MenuNoteCreate(SQLModel):
    kind: MenuNoteKind
    text: str


class MenuNoteRead(MenuNoteBase):
    id: UUID
    user_id: UUID
    created_at: datetime


class OffPlanEntryBase(SQLModel):
    date: dt_date = Field(sa_column=Column(Date, nullable=False, index=True))
    at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    text: str = Field(sa_column=Column(String, nullable=False))
    kcal_est: int | None = Field(default=None, sa_column=Column(Integer, nullable=True))


class OffPlanEntry(OffPlanEntryBase, table=True):
    __tablename__: ClassVar[str] = "off_plan_entries"

    id: UUID | None = Field(
        default=None,
        sa_column=Column(Uuid, primary_key=True, server_default=text("uuidv7()")),
    )
    user_id: UUID = Field(
        sa_column=Column(
            Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
        )
    )


class OffPlanEntryCreate(SQLModel):
    date: dt_date
    at: datetime
    text: str
    kcal_est: int | None = None


class OffPlanEntryRead(OffPlanEntryBase):
    id: UUID
    user_id: UUID
