from typing import Any

from pydantic import BaseModel, field_validator
from sqlmodel import Field, SQLModel


class NutritionSet(SQLModel):
    """Shared nutrition set for products, dish totals, and menu items.

    Macronutrients (kcal, protein_g, fat_g, carbs_g) are required once known.
    Micronutrients (fiber_g, sugar_g, sodium_mg) are optional.
    Missing values are NULL (None), never 0.
    """

    kcal: float | None = Field(default=None, nullable=True)
    protein_g: float | None = Field(default=None, nullable=True)
    fat_g: float | None = Field(default=None, nullable=True)
    carbs_g: float | None = Field(default=None, nullable=True)
    fiber_g: float | None = Field(default=None, nullable=True)
    sugar_g: float | None = Field(default=None, nullable=True)
    sodium_mg: float | None = Field(default=None, nullable=True)


class NutritionEstimate(BaseModel):
    """Gemini-returned nutrition estimate per 100g or portion."""

    kcal: float
    protein_g: float
    fat_g: float
    carbs_g: float
    fiber_g: float | None = None
    sugar_g: float | None = None
    sodium_mg: float | None = None

    @field_validator("kcal", "protein_g", "fat_g", "carbs_g", mode="before")
    @classmethod
    def validate_required_non_negative(cls, v: Any) -> float:
        if v is None:
            raise ValueError("Required nutrition field cannot be null")
        val = float(v)
        if val < 0:
            raise ValueError("Nutrition values must be non-negative")
        return val

    @field_validator("fiber_g", "sugar_g", "sodium_mg", mode="before")
    @classmethod
    def validate_optional_non_negative(cls, v: Any) -> float | None:
        if v is None:
            return None
        val = float(v)
        if val < 0:
            raise ValueError("Nutrition values must be non-negative")
        return val
