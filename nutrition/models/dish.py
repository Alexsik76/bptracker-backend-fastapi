from datetime import date, datetime
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
    Uuid,
    func,
    text,
)
from sqlmodel import Field, SQLModel

from nutrition.models.enums import DishKind, MealType


class DishComponentBase(SQLModel):
    name: str = Field(sa_column=Column(String, nullable=False))
    raw_weight_g: int | None = Field(default=None, sa_column=Column(Integer, nullable=True))
    cooked_weight_g: int | None = Field(default=None, sa_column=Column(Integer, nullable=True))


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


class DishComponentCreate(DishComponentBase):
    pass


class DishComponentRead(DishComponentBase):
    id: UUID
    dish_id: UUID


class DishBase(SQLModel):
    name: str = Field(sa_column=Column(String, nullable=False))
    kind: DishKind = Field(
        sa_column=Column(
            sa.Enum(
                DishKind,
                name="dishkind",
                values_callable=lambda x: [e.value for e in x],
            ),
            nullable=False,
        )
    )
    cooked_weight_g: int | None = Field(default=None, sa_column=Column(Integer, nullable=True))
    remaining_g: int | None = Field(default=None, sa_column=Column(Integer, nullable=True))
    cooked_on: date | None = Field(default=None, sa_column=Column(Date, nullable=True))
    fixed_meal_type: MealType | None = Field(
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
    notes: str | None = Field(default=None, sa_column=Column(String, nullable=True))


class Dish(DishBase, table=True):
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

    __table_args__ = (
        CheckConstraint(
            "(remaining_g IS NULL) OR (remaining_g >= 0)",
            name="chk_dishes_remaining_g_non_negative",
        ),
    )


class DishCreate(DishBase):
    components: list[DishComponentCreate] = []

    @model_validator(mode="after")
    def validate_kind_fields(self) -> "DishCreate":
        if self.kind == DishKind.READY:
            if self.cooked_weight_g is not None and self.cooked_weight_g <= 0:
                raise ValueError("cooked_weight_g must be > 0 when provided")
            if self.remaining_g is None and self.cooked_weight_g is not None:
                self.remaining_g = self.cooked_weight_g
            if self.remaining_g is not None and self.remaining_g < 0:
                raise ValueError("remaining_g cannot be negative")
        elif self.kind == DishKind.CONSTANT:
            if not self.components:
                raise ValueError("components are required for constant dishes")
        return self


class DishRead(DishBase):
    id: UUID
    user_id: UUID
    created_at: datetime
    updated_at: datetime
    components: list[DishComponentRead] = []


class DishUpdate(SQLModel):
    name: str | None = None
    cooked_weight_g: int | None = None
    remaining_g: int | None = None
    cooked_on: date | None = None
    fixed_meal_type: MealType | None = None
    notes: str | None = None
    components: list[DishComponentCreate] | None = None

    @model_validator(mode="after")
    def validate_update_fields(self) -> "DishUpdate":
        if self.remaining_g is not None and self.remaining_g < 0:
            raise ValueError("remaining_g cannot be negative")
        if self.cooked_weight_g is not None and self.cooked_weight_g <= 0:
            raise ValueError("cooked_weight_g must be > 0")
        return self
