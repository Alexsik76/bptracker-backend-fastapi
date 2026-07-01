from datetime import time
from typing import ClassVar
from uuid import UUID

from sqlalchemy import Column, Uuid
from sqlmodel import Field, SQLModel


class ReminderConfigBase(SQLModel):
    morning_time: time
    day_time: time
    evening_time: time
    max_reminders: int
    duration_minutes: int


class ReminderConfig(ReminderConfigBase, table=True):
    __tablename__: ClassVar[str] = "reminder_config"

    # 1:1 with users: user_id is the primary key directly, no separate id column.
    # Plain uuid for now; FK to users is added by a migration when the auth module lands.
    user_id: UUID = Field(sa_column=Column(Uuid, primary_key=True))


class ReminderConfigCreate(ReminderConfigBase):
    pass


class ReminderConfigRead(ReminderConfigBase):
    pass


class ReminderConfigUpdate(SQLModel):
    morning_time: time | None = None
    day_time: time | None = None
    evening_time: time | None = None
    max_reminders: int | None = None
    duration_minutes: int | None = None
