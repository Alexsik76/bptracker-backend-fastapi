from datetime import datetime
from typing import ClassVar
from uuid import UUID

import sqlalchemy as sa
from pydantic import field_validator
from sqlalchemy import Column, DateTime, ForeignKey, Uuid, func
from sqlmodel import Field, SQLModel

from nutrition.models.enums import Sex


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
    weight_variety: float = Field(
        default=0.5,
        sa_column=Column(sa.Float, nullable=False, server_default=sa.text("0.5")),
    )
    weight_balance: float = Field(
        default=0.5,
        sa_column=Column(sa.Float, nullable=False, server_default=sa.text("0.5")),
    )
    weight_goal: float = Field(
        default=0.5,
        sa_column=Column(sa.Float, nullable=False, server_default=sa.text("0.5")),
    )

    @field_validator("weight_variety", "weight_balance", "weight_goal")
    @classmethod
    def validate_weights(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError("Weight must be between 0.0 and 1.0")
        return v


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
