from datetime import datetime
from typing import Any, ClassVar
from uuid import UUID

from sqlalchemy import Column, DateTime, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class AppSetting(SQLModel, table=True):
    __tablename__: ClassVar[str] = "app_settings"

    key: str = Field(sa_column=Column(String, primary_key=True))
    value: Any = Field(sa_column=Column(JSONB, nullable=False))
    updated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    # Deliberately not a FK: the setting must survive removal of the admin account.
    updated_by: UUID | None = Field(default=None, sa_column=Column(Uuid, nullable=True))
