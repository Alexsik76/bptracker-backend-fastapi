from datetime import datetime
from typing import ClassVar
from uuid import UUID

import sqlalchemy as sa
from pydantic import model_validator
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlmodel import Field, SQLModel

from nutrition.models.enums import MealType


class PinnedMealItemBase(SQLModel):
    frequent_dish_id: UUID = Field(
        sa_column=Column(
            Uuid,
            ForeignKey("frequent_dishes.id", ondelete="CASCADE"),
            index=True,
            nullable=False,
        )
    )
    position: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False, server_default=text("0")),
    )


class PinnedMealItem(PinnedMealItemBase, table=True):
    __tablename__: ClassVar[str] = "pinned_meal_items"

    id: UUID | None = Field(
        default=None,
        sa_column=Column(Uuid, primary_key=True, server_default=text("uuidv7()")),
    )
    pinned_meal_id: UUID = Field(
        sa_column=Column(
            Uuid,
            ForeignKey("pinned_meals.id", ondelete="CASCADE"),
            index=True,
            nullable=False,
        )
    )


class PinnedMealItemCreate(SQLModel):
    frequent_dish_id: UUID
    position: int = 0


class PinnedMealItemRead(PinnedMealItemBase):
    id: UUID
    pinned_meal_id: UUID


class PinnedMealBase(SQLModel):
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
    active: bool = Field(
        default=True,
        sa_column=Column(Boolean, nullable=False, server_default=text("true")),
    )


class PinnedMeal(PinnedMealBase, table=True):
    __tablename__: ClassVar[str] = "pinned_meals"

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
        UniqueConstraint(
            "user_id", "meal_type", name="uq_pinned_meals_user_id_meal_type"
        ),
    )


class PinnedMealUpsert(SQLModel):
    active: bool = True
    items: list[PinnedMealItemCreate] = []

    @model_validator(mode="after")
    def validate_items(self) -> "PinnedMealUpsert":
        if not self.items:
            raise ValueError("A pinned meal must have at least 1 item")
        return self


PinnedMealCreate = PinnedMealUpsert


class PinnedMealRead(PinnedMealBase):
    id: UUID
    user_id: UUID
    created_at: datetime
    updated_at: datetime
    items: list[PinnedMealItemRead] = []
