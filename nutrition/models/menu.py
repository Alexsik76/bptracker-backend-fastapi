import re
from datetime import date as dt_date
from datetime import datetime
from typing import ClassVar
from uuid import UUID

import sqlalchemy as sa
from pydantic import model_validator
from sqlalchemy import (
    Boolean,
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
    MealType,
    MenuItemOrigin,
    MenuStatus,
    NutritionSource,
)
from nutrition.models.notes_and_offplan import MenuNoteRead, OffPlanEntryRead
from nutrition.models.nutrition import NutritionSet


class MenuBase(SQLModel):
    date: dt_date = Field(sa_column=Column(Date, nullable=False, index=True))
    meal_type: MealType = Field(
        sa_column=Column(
            sa.Enum(
                MealType,
                name="mealtype",
                values_callable=lambda x: [e.value for e in x],
            ),
            nullable=False,
        )
    )
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
    eaten_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
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
        UniqueConstraint(
            "user_id", "date", "meal_type", name="uq_menus_user_id_date_meal_type"
        ),
    )


class MenuDay(SQLModel, table=True):
    __tablename__: ClassVar[str] = "menu_days"

    user_id: UUID = Field(
        sa_column=Column(
            Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, nullable=False
        )
    )
    date: dt_date = Field(
        sa_column=Column(Date, primary_key=True, nullable=False)
    )
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), server_default=func.now(), nullable=False),
    )


class MenuItemBase(NutritionSet):
    dish_id: UUID | None = Field(
        default=None,
        sa_column=Column(
            Uuid, ForeignKey("dishes.id", ondelete="SET NULL"), nullable=True
        ),
    )
    title: str = Field(sa_column=Column(String, nullable=False))
    portion_g: int | None = Field(default=None, sa_column=Column(Integer, nullable=True))
    portion_text: str | None = Field(default=None, sa_column=Column(String, nullable=True))
    nutrition_source: NutritionSource | None = Field(
        default=None,
        sa_column=Column(
            sa.Enum(
                NutritionSource,
                name="nutritionsource",
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


class MenuItemCreate(NutritionSet):
    dish_id: UUID | None = None
    title: str | None = None
    portion_g: int | None = None
    portion_text: str | None = None
    position: int = 0

    @model_validator(mode="after")
    def validate_reference(self) -> "MenuItemCreate":
        if self.dish_id is None:
            if not self.title or not self.title.strip():
                raise ValueError("Title is required when dish_id is not provided")
            self.title = self.title.strip()
        elif self.title:
            self.title = self.title.strip()
        return self


class MenuItemRead(MenuItemBase):
    id: UUID
    menu_id: UUID
    updated_at: datetime
    incomplete: bool = False


class MenuItemUpdate(NutritionSet):
    dish_id: UUID | None = None
    title: str | None = None
    portion_g: int | None = None
    portion_text: str | None = None
    position: int | None = None


class MenuRead(SQLModel):
    id: UUID | None = None
    user_id: UUID
    date: dt_date
    meal_type: MealType
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
            it.kcal is None
            for it in self.items
        )
        return self


def is_meal_generatable(menu: MenuRead) -> bool:
    """Check if a canvas meal can be generated.

    A meal is generatable if it is on the canvas, not pinned,
    not marked as eaten, and not missed.
    """
    if getattr(menu, "pinned", False) or getattr(menu, "pinned_meal_id", None) is not None:
        return False
    if menu.status == MenuStatus.MISSED:
        return False
    return menu.status != MenuStatus.EATEN and menu.eaten_at is None


def compute_day_generation_flags(menus: list[MenuRead]) -> tuple[bool, bool]:
    """Compute (can_generate_day, day_generation_replaces_manual) for day canvas."""
    can_generate = any(is_meal_generatable(m) for m in menus)
    replaces_manual = any(
        is_meal_generatable(m)
        and any(it.origin == MenuItemOrigin.MANUAL for it in m.items)
        for m in menus
    )
    return can_generate, replaces_manual


class MenuUpdate(SQLModel):
    eaten_at: datetime | None = None


class MealCreate(SQLModel):
    meal_type: MealType
    time: str

    @model_validator(mode="after")
    def validate_time(self) -> "MealCreate":
        self.time = self.time.strip()
        if not re.match(r"^(?:[01]\d|2[0-3]):[0-5]\d$", self.time):
            raise ValueError("Time must be in HH:MM format (00:00 to 23:59)")
        return self


class GenerationRequest(SQLModel):
    scope: GenerationScope = GenerationScope.DAY
    meal_type: MealType | None = None
    now: datetime | None = None


class GenerationResultRead(SQLModel):
    menus: list[MenuRead]
    rationale: str = ""
    shopping_list: list[str] = []


class AvailablePinRead(SQLModel):
    meal_type: MealType
    pinned_meal_id: UUID


class DayMenuRead(SQLModel):
    date: dt_date
    day_created: bool = False
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

