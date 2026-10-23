from datetime import date as dt_date
from datetime import datetime
from typing import ClassVar
from uuid import UUID

import sqlalchemy as sa
from pydantic import model_validator
from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlmodel import Field, SQLModel

from nutrition.models.enums import GenerationScope, MenuItemOrigin, MenuStatus


class MenuBase(SQLModel):
    date: dt_date = Field(sa_column=Column(Date, nullable=False))
    slot: str = Field(sa_column=Column(String, nullable=False))
    status: MenuStatus = Field(
        default=MenuStatus.PLANNED,
        sa_column=Column(
            sa.Enum(
                MenuStatus,
                name="menustatus",
                values_callable=lambda x: [e.value for e in x],
            ),
            nullable=False,
            default=MenuStatus.PLANNED,
        ),
    )
    eaten_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    regenerations: int = Field(default=0, sa_column=Column(Integer, nullable=False))


class Menu(MenuBase, table=True):
    __tablename__: ClassVar[str] = "menus"

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

    __table_args__ = (
        UniqueConstraint("user_id", "date", "slot", name="uq_menus_user_id_date_slot"),
    )


class MenuItemBase(SQLModel):
    dish_id: UUID | None = Field(
        default=None,
        sa_column=Column(
            Uuid, ForeignKey("dishes.id", ondelete="SET NULL"), nullable=True
        ),
    )
    product_id: UUID | None = Field(
        default=None,
        sa_column=Column(
            Uuid, ForeignKey("products.id", ondelete="SET NULL"), nullable=True
        ),
    )
    title: str = Field(sa_column=Column(String, nullable=False))
    portion_g: int | None = Field(default=None, sa_column=Column(Integer, nullable=True))
    portion_text: str | None = Field(default=None, sa_column=Column(String, nullable=True))
    kcal_est: int | None = Field(default=None, sa_column=Column(Integer, nullable=True))
    origin: MenuItemOrigin = Field(
        default=MenuItemOrigin.MANUAL,
        sa_column=Column(
            sa.Enum(
                MenuItemOrigin,
                name="menuitemorigin",
                values_callable=lambda x: [e.value for e in x],
            ),
            nullable=False,
        ),
    )
    position: int = Field(default=0, sa_column=Column(Integer, nullable=False))


class MenuItem(MenuItemBase, table=True):
    __tablename__: ClassVar[str] = "menu_items"

    id: UUID | None = Field(
        default=None,
        sa_column=Column(Uuid, primary_key=True, server_default=text("uuidv7()")),
    )
    menu_id: UUID = Field(
        sa_column=Column(
            Uuid, ForeignKey("menus.id", ondelete="CASCADE"), index=True, nullable=False
        )
    )

    __table_args__ = (
        CheckConstraint(
            "NOT (dish_id IS NOT NULL AND product_id IS NOT NULL)",
            name="chk_menu_items_single_reference",
        ),
    )


class MenuItemCreate(SQLModel):
    dish_id: UUID | None = None
    product_id: UUID | None = None
    title: str | None = None
    portion_g: int | None = None
    portion_text: str | None = None
    kcal_est: int | None = None
    position: int = 0

    @model_validator(mode="after")
    def validate_reference(self) -> "MenuItemCreate":
        has_dish = self.dish_id is not None
        has_product = self.product_id is not None
        if has_dish == has_product:
            raise ValueError("Exactly one of dish_id or product_id must be provided")
        return self


class MenuItemRead(MenuItemBase):
    id: UUID
    menu_id: UUID


class MenuItemUpdate(SQLModel):
    dish_id: UUID | None = None
    product_id: UUID | None = None
    title: str | None = None
    portion_g: int | None = None
    portion_text: str | None = None
    kcal_est: int | None = None
    position: int | None = None


class MenuRead(SQLModel):
    id: UUID
    user_id: UUID
    date: dt_date
    slot: str
    status: MenuStatus
    eaten_at: datetime | None = None
    regenerations: int
    created_at: datetime
    updated_at: datetime
    items: list[MenuItemRead] = []


class MenuUpdate(SQLModel):
    status: MenuStatus | None = None
    eaten_at: datetime | None = None


class GenerationRequest(SQLModel):
    scope: GenerationScope = GenerationScope.DAY
    slot: str | None = None


class GenerationResultRead(SQLModel):
    menus: list[MenuRead]
    rationale: str = ""
    shopping_list: list[str] = []
