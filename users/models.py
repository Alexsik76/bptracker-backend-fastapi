from datetime import datetime
from typing import ClassVar
from uuid import UUID

from pydantic import EmailStr, field_validator
from sqlalchemy import Column, DateTime, String, Uuid, func, text
from sqlmodel import Field, SQLModel


class UserBase(SQLModel):
    email: EmailStr = Field(index=True, sa_column_kwargs={"unique": True})


class User(UserBase, table=True):
    __tablename__: ClassVar[str] = "users"

    id: UUID | None = Field(
        default=None,
        sa_column=Column(Uuid, primary_key=True, server_default=text("uuidv7()")),
    )
    display_name: str | None = Field(
        default=None,
        sa_column=Column(String(120), nullable=True),
    )
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), server_default=func.now()),
    )


class UserRead(SQLModel):
    id: UUID
    email: EmailStr
    display_name: str | None = None
    created_at: datetime
    is_admin: bool = False


class UserUpdate(SQLModel):
    display_name: str | None = Field(default=None, max_length=120)

    @field_validator("display_name", mode="before")
    @classmethod
    def sanitize_display_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if isinstance(v, str):
            v = v.strip()
            return v if v else None
        return v
