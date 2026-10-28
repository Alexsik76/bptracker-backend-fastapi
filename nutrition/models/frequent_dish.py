from datetime import date as dt_date
from datetime import datetime
from typing import ClassVar
from uuid import UUID

from pydantic import model_validator
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Uuid,
    func,
    text,
)
from sqlmodel import Field, SQLModel

from nutrition.models.dish import DishComponentCreate
from nutrition.models.nutrition import NutritionSet


class FrequentDishComponentBase(SQLModel):
    product_id: UUID = Field(
        sa_column=Column(
            Uuid, ForeignKey("products.id", ondelete="CASCADE"), index=True, nullable=False
        )
    )
    weight_g: int = Field(sa_column=Column(Integer, nullable=False))


class FrequentDishComponent(FrequentDishComponentBase, table=True):
    __tablename__: ClassVar[str] = "frequent_dish_components"

    id: UUID | None = Field(
        default=None,
        sa_column=Column(Uuid, primary_key=True, server_default=text("uuidv7()")),
    )
    frequent_dish_id: UUID = Field(
        sa_column=Column(
            Uuid,
            ForeignKey("frequent_dishes.id", ondelete="CASCADE"),
            index=True,
            nullable=False,
        )
    )

    __table_args__ = (
        CheckConstraint(
            "weight_g > 0",
            name="chk_frequent_dish_components_weight_g_positive",
        ),
    )


class FrequentDishComponentCreate(DishComponentCreate):
    pass


class FrequentDishComponentRead(FrequentDishComponentBase):
    id: UUID
    frequent_dish_id: UUID


class FrequentDishBase(SQLModel):
    recipe_id: UUID = Field(
        sa_column=Column(
            Uuid, ForeignKey("recipes.id", ondelete="RESTRICT"), index=True, nullable=False
        )
    )
    cooked_weight_g: int = Field(sa_column=Column(Integer, nullable=False))
    nutrition_overridden: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default=text("false")),
    )


class FrequentDish(FrequentDishBase, NutritionSet, table=True):
    __tablename__: ClassVar[str] = "frequent_dishes"

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
        CheckConstraint(
            "cooked_weight_g > 0",
            name="chk_frequent_dishes_cooked_weight_g_positive",
        ),
    )


class FrequentDishCreate(NutritionSet):
    recipe_id: UUID
    cooked_weight_g: int
    components: list[FrequentDishComponentCreate] = []

    @model_validator(mode="after")
    def validate_create(self) -> "FrequentDishCreate":
        if self.cooked_weight_g <= 0:
            raise ValueError("cooked_weight_g must be > 0")
        return self


class FrequentDishUpdate(NutritionSet):
    recipe_id: UUID | None = None
    cooked_weight_g: int | None = None
    components: list[FrequentDishComponentCreate] | None = None
    reset_nutrition: bool | None = None

    @model_validator(mode="after")
    def validate_update(self) -> "FrequentDishUpdate":
        if self.cooked_weight_g is not None and self.cooked_weight_g <= 0:
            raise ValueError("cooked_weight_g must be > 0")
        return self


class FrequentDishRead(NutritionSet):
    id: UUID
    user_id: UUID
    recipe_id: UUID
    recipe_name: str
    cooked_weight_g: int
    nutrition_overridden: bool = False
    created_at: datetime
    updated_at: datetime
    components: list[FrequentDishComponentRead] = []


class FrequentDishCookRequest(SQLModel):
    cooked_on: dt_date
