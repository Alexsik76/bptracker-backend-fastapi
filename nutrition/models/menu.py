import re
from datetime import date as dt_date
from datetime import datetime
from typing import ClassVar, Literal
from uuid import UUID

import sqlalchemy as sa
from pydantic import model_validator
from sqlalchemy import (
    Boolean,
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

from nutrition.models.enums import (
    GenerationScope,
    KcalSource,
    MenuItemOrigin,
    MenuItemStatus,
    MenuStatus,
)
from nutrition.models.notes_and_offplan import MenuNoteRead, OffPlanEntryRead


class MenuBase(SQLModel):
    date: dt_date = Field(sa_column=Column(Date, nullable=False, index=True))
    slot: str = Field(sa_column=Column(String, nullable=False))
    time: str | None = Field(default=None, sa_column=Column(String, nullable=True))
    regenerations: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False, server_default=text("0")),
    )


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
    removed: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default=text("false")),
    )
    pinned_meal_id: UUID | None = Field(
        default=None,
        sa_column=Column(
            Uuid, ForeignKey("pinned_meals.id", ondelete="SET NULL"), nullable=True
        ),
    )
    pin_dismissed: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default=text("false")),
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
    kcal_source: KcalSource | None = Field(
        default=None,
        sa_column=Column(
            sa.Enum(
                KcalSource,
                name="kcalsource",
                values_callable=lambda x: [e.value for e in x],
            ),
            nullable=True,
        ),
    )
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
    position: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False, server_default=text("0")),
    )
    status: MenuItemStatus | None = Field(
        default=None,
        sa_column=Column(
            sa.Enum(
                MenuItemStatus,
                name="menuitemstatus",
                values_callable=lambda x: [e.value for e in x],
            ),
            nullable=True,
        ),
    )
    eaten_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )


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
    status: MenuItemStatus | None = None
    eaten_at: datetime | None = None

    @model_validator(mode="after")
    def validate_reference(self) -> "MenuItemCreate":
        has_dish = self.dish_id is not None
        has_product = self.product_id is not None
        if has_dish and has_product:
            raise ValueError("Both dish_id and product_id cannot be provided at once")
        if not has_dish and not has_product:
            if not self.title or not self.title.strip():
                raise ValueError(
                    "Title is required when neither dish_id nor product_id is provided"
                )
            self.title = self.title.strip()
        return self


class MenuItemRead(MenuItemBase):
    id: UUID
    menu_id: UUID
    incomplete: bool = False


class MenuItemUpdate(SQLModel):
    dish_id: UUID | None = None
    product_id: UUID | None = None
    title: str | None = None
    portion_g: int | None = None
    portion_text: str | None = None
    kcal_est: int | None = None
    position: int | None = None
    status: MenuItemStatus | None = None
    eaten_at: datetime | None = None


class MenuRead(SQLModel):
    id: UUID | None = None
    user_id: UUID
    date: dt_date
    slot: str
    time: str | None = None
    status: MenuStatus
    pinned: bool = False
    eaten_at: datetime | None = None
    regenerations: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None
    items: list[MenuItemRead] = []
    kcal_incomplete: bool = False

    @model_validator(mode="after")
    def compute_kcal_incomplete(self) -> "MenuRead":
        self.kcal_incomplete = any(
            it.status != MenuItemStatus.SKIPPED and it.kcal_est is None
            for it in self.items
        )
        return self


def is_meal_generatable(menu: MenuRead) -> bool:
    """Check if a canvas meal can be generated.

    A meal is generatable if it is on the canvas, not pinned,
    and has no items or at least one unmarked item.
    """
    if getattr(menu, "pinned", False) or getattr(menu, "pinned_meal_id", None) is not None:
        return False
    if not menu.items:
        return True
    return any(item.status is None for item in menu.items)


def compute_day_generation_flags(menus: list[MenuRead]) -> tuple[bool, bool]:
    """Compute (can_generate_day, day_generation_replaces_manual) for day canvas."""
    can_generate = any(is_meal_generatable(m) for m in menus)
    replaces_manual = any(
        is_meal_generatable(m)
        and any(
            it.status is None and it.origin == MenuItemOrigin.MANUAL
            for it in m.items
        )
        for m in menus
    )
    return can_generate, replaces_manual


class MenuUpdate(SQLModel):
    status: Literal["eaten", "planned"] | None = None
    eaten_at: datetime | None = None


class MealCreate(SQLModel):
    slot: str
    time: str

    @model_validator(mode="after")
    def validate_slot_and_time(self) -> "MealCreate":
        self.slot = self.slot.strip()
        if not self.slot:
            raise ValueError("Slot name must not be empty")
        self.time = self.time.strip()
        if not re.match(r"^(?:[01]\d|2[0-3]):[0-5]\d$", self.time):
            raise ValueError("Time must be in HH:MM format (00:00 to 23:59)")
        return self


class GenerationRequest(SQLModel):
    scope: GenerationScope = GenerationScope.DAY
    slot: str | None = None


class GenerationResultRead(SQLModel):
    menus: list[MenuRead]
    rationale: str = ""
    shopping_list: list[str] = []


class AvailablePinRead(SQLModel):
    slot: str
    pinned_meal_id: UUID


class DayMenuRead(SQLModel):
    date: dt_date
    menus: list[MenuRead] = []
    notes: list[MenuNoteRead] = []
    off_plan: list[OffPlanEntryRead] = []
    available_pins: list[AvailablePinRead] = []
    kcal_incomplete: bool = False
    can_generate_day: bool = False
    day_generation_replaces_manual: bool = False

    @model_validator(mode="after")
    def compute_canvas_flags(self) -> "DayMenuRead":
        self.kcal_incomplete = any(m.kcal_incomplete for m in self.menus)
        self.can_generate_day, self.day_generation_replaces_manual = (
            compute_day_generation_flags(self.menus)
        )
        return self

