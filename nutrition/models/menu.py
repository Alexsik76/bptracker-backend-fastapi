from datetime import date as dt_date
from datetime import datetime
from typing import ClassVar
from uuid import UUID

import sqlalchemy as sa
from pydantic import model_validator
from sqlalchemy import (
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

from nutrition.clock import utc_today
from nutrition.models.enums import (
    MealType,
    MenuItemOrigin,
    MenuOrigin,
    MenuState,
    NutritionSource,
)
from nutrition.models.evaluation import MealEvaluationRead
from nutrition.models.nutrition import NutritionSet


class MenuBase(SQLModel):
    date: dt_date = Field(
        default_factory=utc_today,
        sa_column=Column(Date, nullable=False, index=True),
    )
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
    origin: MenuOrigin = Field(
        default=MenuOrigin.MANUAL,
        sa_column=Column(
            sa.Enum(
                MenuOrigin,
                name="menuorigin",
                values_callable=lambda x: [e.value for e in x],
            ),
            nullable=False,
            server_default=text("'manual'"),
        ),
    )
    state: MenuState = Field(
        default=MenuState.EATEN,
        sa_column=Column(
            sa.Enum(
                MenuState,
                name="menustate",
                values_callable=lambda x: [e.value for e in x],
            ),
            nullable=False,
            server_default=text("'eaten'"),
        ),
    )
    eaten_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    pinned_dish_id: UUID | None = Field(
        default=None,
        sa_column=Column(
            Uuid,
            ForeignKey("dishes.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
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
    nutrition_source: NutritionSource | None = None
    origin: MenuItemOrigin = MenuItemOrigin.MANUAL
    position: int = 0

    @model_validator(mode="after")
    def validate_reference(self) -> "MenuItemCreate":
        if self.dish_id is None:
            if not self.title or not self.title.strip():
                raise ValueError("Title is required when dish_id is not provided")
            self.title = self.title.strip()
        elif self.title:
            self.title = self.title.strip()
        if self.portion_g is not None and not self.portion_text:
            self.portion_text = f"{self.portion_g} г"
        return self


class MenuItemRead(MenuItemBase):
    id: UUID
    menu_id: UUID
    created_at: datetime
    updated_at: datetime


class MenuItemUpdate(NutritionSet):
    dish_id: UUID | None = None
    title: str | None = None
    portion_g: int | None = None
    portion_text: str | None = None
    nutrition_source: NutritionSource | None = None
    position: int | None = None


class MenuRead(MenuBase):
    id: UUID
    user_id: UUID
    created_at: datetime
    updated_at: datetime
    items: list[MenuItemRead] = []
    evaluations: list[MealEvaluationRead] = []


class MenuCreate(SQLModel):
    date: dt_date
    meal_type: MealType
    origin: MenuOrigin = MenuOrigin.MANUAL
    state: MenuState = MenuState.EATEN
    eaten_at: datetime | None = None
    pinned_dish_id: UUID | None = None
    items: list[MenuItemCreate] = []


class MenuUpdate(SQLModel):
    date: dt_date | None = None
    meal_type: MealType | None = None
    origin: MenuOrigin | None = None
    state: MenuState | None = None
    eaten_at: datetime | None = None
    pinned_dish_id: UUID | None = None


class PinnedDishDay(SQLModel, table=True):
    __tablename__: ClassVar[str] = "pinned_dish_days"

    id: UUID | None = Field(
        default=None,
        sa_column=Column(Uuid, primary_key=True, server_default=text("uuidv7()")),
    )
    user_id: UUID = Field(
        sa_column=Column(
            Uuid,
            ForeignKey("users.id", ondelete="CASCADE"),
            index=True,
            nullable=False,
        )
    )
    dish_id: UUID = Field(
        sa_column=Column(
            Uuid,
            ForeignKey("dishes.id", ondelete="CASCADE"),
            index=True,
            nullable=False,
        )
    )
    date: dt_date = Field(
        sa_column=Column(Date, index=True, nullable=False),
    )
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), server_default=func.now(), nullable=False),
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id", "dish_id", "date", name="uq_pinned_dish_days_user_id_dish_id_date"
        ),
    )

