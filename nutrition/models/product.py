from datetime import datetime
from typing import ClassVar
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Uuid, func, text
from sqlmodel import Field, SQLModel

from nutrition.models.enums import ProductAvailability, ProductPreference


class ProductBase(SQLModel):
    name: str = Field(sa_column=Column(String, nullable=False))
    availability: ProductAvailability = Field(
        default=ProductAvailability.IN_STOCK,
        sa_column=Column(
            sa.Enum(
                ProductAvailability,
                name="productavailability",
                values_callable=lambda x: [e.value for e in x],
            ),
            nullable=False,
        ),
    )
    preference: ProductPreference = Field(
        default=ProductPreference.NEUTRAL,
        sa_column=Column(
            sa.Enum(
                ProductPreference,
                name="productpreference",
                values_callable=lambda x: [e.value for e in x],
            ),
            nullable=False,
        ),
    )
    eat_raw: bool = Field(default=False, sa_column=Column(Boolean, nullable=False))
    count: int | None = Field(default=None, sa_column=Column(Integer, nullable=True))
    daily_limit_g: int | None = Field(default=None, sa_column=Column(Integer, nullable=True))


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


class ProductCreate(ProductBase):
    pass


class ProductRead(ProductBase):
    id: UUID
    user_id: UUID
    created_at: datetime
    updated_at: datetime


class ProductUpdate(SQLModel):
    name: str | None = None
    availability: ProductAvailability | None = None
    preference: ProductPreference | None = None
    eat_raw: bool | None = None
    count: int | None = None
    daily_limit_g: int | None = None
