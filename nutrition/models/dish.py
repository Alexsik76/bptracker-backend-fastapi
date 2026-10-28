from datetime import date, datetime
from typing import ClassVar
from uuid import UUID

from pydantic import model_validator
from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Uuid,
    func,
    text,
)
from sqlmodel import Field, SQLModel

from nutrition.models.nutrition import NutritionSet


class StockWarning(SQLModel):
    product_id: UUID
    product_name: str
    missing_g: int


class DishComponentBase(SQLModel):
    product_id: UUID = Field(
        sa_column=Column(
            Uuid, ForeignKey("products.id", ondelete="CASCADE"), index=True, nullable=False
        )
    )
    weight_g: int = Field(sa_column=Column(Integer, nullable=False))


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


class NewProductInput(SQLModel):
    name: str
    stock_g: int | None = None


class DishComponentCreate(SQLModel):
    product_id: UUID | None = None
    new_product: NewProductInput | None = None
    weight_g: int

    @model_validator(mode="after")
    def validate_component(self) -> "DishComponentCreate":
        if self.product_id is None and self.new_product is None:
            raise ValueError("Either product_id or new_product must be provided")
        if self.product_id is not None and self.new_product is not None:
            raise ValueError("Cannot provide both product_id and new_product")
        if self.weight_g <= 0:
            raise ValueError("weight_g must be > 0")
        return self


class DishComponentRead(DishComponentBase):
    id: UUID
    dish_id: UUID


class NewRecipeInput(SQLModel):
    name: str


class DishBase(SQLModel):
    name: str = Field(sa_column=Column(String, nullable=False))
    cooked_weight_g: int = Field(sa_column=Column(Integer, nullable=False))
    remaining_g: int = Field(sa_column=Column(Integer, nullable=False))
    cooked_on: date | None = Field(default=None, sa_column=Column(Date, nullable=True))
    notes: str | None = Field(default=None, sa_column=Column(String, nullable=True))


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
    recipe_id: UUID = Field(
        sa_column=Column(
            Uuid, ForeignKey("recipes.id", ondelete="RESTRICT"), index=True, nullable=False
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
            name="chk_dishes_cooked_weight_g_positive",
        ),
        CheckConstraint(
            "remaining_g >= 0",
            name="chk_dishes_remaining_g_non_negative",
        ),
    )


class DishCreate(NutritionSet):
    name: str | None = None
    recipe_id: UUID | None = None
    new_recipe: NewRecipeInput | None = None
    components: list[DishComponentCreate] = []
    cooked_weight_g: int
    remaining_g: int | None = None
    cooked_on: date | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def validate_fields(self) -> "DishCreate":
        if self.recipe_id is None and self.new_recipe is None:
            if self.name and self.name.strip():
                self.new_recipe = NewRecipeInput(name=self.name.strip())
            else:
                raise ValueError("Either recipe_id or new_recipe must be provided")
        elif self.recipe_id is not None and self.new_recipe is not None:
            raise ValueError("Cannot provide both recipe_id and new_recipe")

        if self.cooked_weight_g <= 0:
            raise ValueError("cooked_weight_g must be > 0")

        if self.remaining_g is None:
            self.remaining_g = self.cooked_weight_g
        elif self.remaining_g < 0:
            raise ValueError("remaining_g cannot be negative")

        return self


class DishRead(DishBase, NutritionSet):
    id: UUID
    user_id: UUID
    recipe_id: UUID
    created_at: datetime
    updated_at: datetime
    components: list[DishComponentRead] = []
    stock_warnings: list[StockWarning] = []


class DishUpdate(NutritionSet):
    name: str | None = None
    cooked_weight_g: int | None = None
    remaining_g: int | None = None
    cooked_on: date | None = None
    notes: str | None = None
    components: list[DishComponentCreate] | None = None

    @model_validator(mode="after")
    def validate_update_fields(self) -> "DishUpdate":
        if self.remaining_g is not None and self.remaining_g < 0:
            raise ValueError("remaining_g cannot be negative")
        if self.cooked_weight_g is not None and self.cooked_weight_g <= 0:
            raise ValueError("cooked_weight_g must be > 0")
        return self
