from datetime import datetime
from typing import Any, ClassVar
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import Column, DateTime, ForeignKey, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import TypeDecorator
from sqlmodel import Field, SQLModel

from nutrition.models.enums import Sex


class MealSchedule(SQLModel):
    name: str
    time: str


class MealsJSON(TypeDecorator):
    impl = JSONB
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return []
        if isinstance(value, list):
            return [
                item.model_dump() if hasattr(item, "model_dump") else item
                for item in value
            ]
        return value

    def process_result_value(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return []
        if isinstance(value, list):
            return [
                MealSchedule.model_validate(item) if isinstance(item, dict) else item
                for item in value
            ]
        return value


class NutritionProfileBase(SQLModel):
    sex: Sex = Field(
        sa_column=Column(
            sa.Enum(Sex, name="sex", values_callable=lambda x: [e.value for e in x]),
            nullable=False,
        )
    )
    birth_year: int
    height_cm: int
    weight_kg: float
    target_weight_kg: float
    pace_kg_per_week: float
    restrictions: str | None = None
    dislikes: str | None = None
    meals: list[MealSchedule] = Field(
        default=[],
        sa_column=Column(MealsJSON, nullable=False),
    )


class NutritionProfile(NutritionProfileBase, table=True):
    __tablename__: ClassVar[str] = "nutrition_profiles"

    user_id: UUID = Field(
        sa_column=Column(
            Uuid,
            ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        )
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


class NutritionProfileUpsert(NutritionProfileBase):
    pass


class NutritionProfileRead(NutritionProfileBase):
    user_id: UUID
    updated_at: datetime
