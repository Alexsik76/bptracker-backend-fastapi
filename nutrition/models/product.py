from datetime import datetime
from typing import ClassVar
from uuid import UUID

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, Uuid, func, text
from sqlmodel import Field, SQLModel


class ProductBase(SQLModel):
    name: str = Field(sa_column=Column(String, nullable=False))
    available: bool = Field(
        default=True,
        sa_column=Column(Boolean, nullable=False, server_default=text("true")),
    )
    note: str | None = Field(default=None, sa_column=Column(String, nullable=True))


class Product(ProductBase, table=True):
    __tablename__: ClassVar[str] = "products"

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
    updated_at: datetime | None = Field(
        default=None,
        sa_column=Column(
            DateTime(timezone=True),
            server_default=func.now(),
            onupdate=func.now(),
            nullable=False,
        ),
    )


class ProductCreate(SQLModel):
    name: str
    available: bool = True
    note: str | None = None


class ProductRead(ProductBase):
    id: UUID
    user_id: UUID
    created_at: datetime
    updated_at: datetime


class ProductUpdate(SQLModel):
    name: str | None = None
    available: bool | None = None
    note: str | None = None
