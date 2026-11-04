from datetime import datetime
from typing import Any, ClassVar
from uuid import UUID

import sqlalchemy as sa
from pydantic import field_validator, model_validator
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Uuid,
    func,
    text,
)
from sqlmodel import Field, SQLModel

from nutrition.models.enums import MealType
from nutrition.models.nutrition import NutritionSet


class DishComponentBase(SQLModel):
    name: str = Field(sa_column=Column(String(200), nullable=False), max_length=200)
    weight_g: int = Field(sa_column=Column(Integer, nullable=False))

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if isinstance(v, str):
            v = v.strip()
        if not v:
            raise ValueError("name must not be empty")
        return v

    @field_validator("weight_g")
    @classmethod
    def validate_weight_g(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("weight_g must be > 0")
        return v


class DishComponent(DishComponentBase, table=True):
    __tablename__: ClassVar[str] = "dish_components"

    id: UUID | None = Field(
        default=None,
        sa_column=Column(Uuid, primary_key=True, server_default=text("uuidv7()")),
    )
    dish_id: UUID = Field(
        sa_column=Column(
            Uuid, ForeignKey("dishes.id", ondelete="CASCADE"), index=True, nullable=False
        )
    )

    __table_args__ = (
        CheckConstraint("weight_g > 0", name="chk_dish_components_weight_g_positive"),
    )


class DishComponentCreate(DishComponentBase):
    pass


class DishComponentRead(DishComponentBase):
    id: UUID
    dish_id: UUID


class DishBase(SQLModel):
    name: str = Field(sa_column=Column(String, nullable=False))
    is_ready: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default=text("false")),
    )
    pinned_meal_type: MealType | None = Field(
        default=None,
        sa_column=Column(
            sa.Enum(
                MealType,
                name="mealtype",
                values_callable=lambda x: [e.value for e in x],
            ),
            nullable=True,
        ),
    )
    nutrition_overridden: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default=text("false")),
    )
    note: str | None = Field(default=None, sa_column=Column(String, nullable=True))
    cooked_weight_g: int | None = Field(
        default=None,
        sa_column=Column(Integer, nullable=True),
    )

    @field_validator("cooked_weight_g")
    @classmethod
    def validate_cooked_weight_g(cls, v: int | None) -> int | None:
        if v is not None and v <= 0:
            raise ValueError("cooked_weight_g must be > 0")
        return v


class Dish(DishBase, NutritionSet, table=True):
    __tablename__: ClassVar[str] = "dishes"

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


class DishCreate(NutritionSet):
    name: str
    is_ready: bool = False
    pinned_meal_type: MealType | None = None
    nutrition_overridden: bool = False
    note: str | None = None
    cooked_weight_g: int | None = None
    components: list[DishComponentCreate] = []

    @field_validator("cooked_weight_g")
    @classmethod
    def validate_cooked_weight_g(cls, v: int | None) -> int | None:
        if v is not None and v <= 0:
            raise ValueError("cooked_weight_g must be > 0")
        return v

    @model_validator(mode="before")
    @classmethod
    def handle_legacy_notes(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "notes" in data and "note" not in data:
                data["note"] = data["notes"]
        return data


class DishRead(DishBase, NutritionSet):
    id: UUID
    user_id: UUID
    created_at: datetime
    updated_at: datetime
    components: list[DishComponentRead] = []


class DishUpdate(NutritionSet):
    name: str | None = None
    is_ready: bool | None = None
    pinned_meal_type: MealType | None = None
    nutrition_overridden: bool | None = None
    note: str | None = None
    cooked_weight_g: int | None = None
    components: list[DishComponentCreate] | None = None
    reset_nutrition: bool = False

    @field_validator("cooked_weight_g")
    @classmethod
    def validate_cooked_weight_g(cls, v: int | None) -> int | None:
        if v is not None and v <= 0:
            raise ValueError("cooked_weight_g must be > 0")
        return v

    @model_validator(mode="before")
    @classmethod
    def handle_legacy_notes(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "notes" in data and "note" not in data:
                data["note"] = data["notes"]
        return data
