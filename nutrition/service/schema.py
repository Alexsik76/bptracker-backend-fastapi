from collections.abc import Iterable
from copy import deepcopy
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from nutrition.models import MealType


class GeneratedMenuItem(BaseModel):
    meal_type: MealType
    dish_id: UUID | None = None
    title: str
    portion_g: int = Field(gt=0)
    portion_text: str | None = None
    kcal_est: int | None = None
    position: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_reference(self) -> "GeneratedMenuItem":
        if not self.portion_text:
            self.portion_text = f"{self.portion_g} г"
        return self


class GenerationResponse(BaseModel):
    rationale: str = ""
    shopping_list: list[str] = Field(default_factory=list)
    items: list[GeneratedMenuItem] = Field(default_factory=list)


GEMINI_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "rationale": {
            "type": "STRING",
            "description": "Коротке обґрунтування структури та калорійності меню",
        },
        "shopping_list": {
            "type": "ARRAY",
            "items": {"type": "STRING"},
            "description": "Список продуктів, які бажано докупити, якщо є дефіцит або дисбаланс",
        },
        "items": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "meal_type": {
                        "type": "STRING",
                        "description": (
                            "Тип прийому їжі: breakfast, lunch, dinner, "
                            "morning_snack, afternoon_snack, evening_snack"
                        ),
                    },
                    "dish_id": {
                        "type": "STRING",
                        "description": "UUID наявної страви зі списку наявних страв",
                    },
                    "title": {
                        "type": "STRING",
                        "description": "Назва страви",
                    },
                    "portion_g": {
                        "type": "INTEGER",
                        "description": "Вага порції у грамах (обов'язково додатне число)",
                    },
                    "portion_text": {
                        "type": "STRING",
                        "description": (
                            "Текстовий опис порції (наприклад '200 г' чи '1 порція')"
                        ),
                    },
                    "position": {
                        "type": "INTEGER",
                        "description": "Порядковий номер позиції в межах прийому їжі (1, 2, ...)",
                    },
                },
                "required": [
                    "meal_type",
                    "title",
                    "portion_g",
                    "portion_text",
                    "position",
                ],
            },
        },
    },
    "required": ["rationale", "shopping_list", "items"],
}


def build_gemini_response_schema(
    dish_ids: Iterable[UUID] | None = None,
    allowed_meal_types: Iterable[MealType] | None = None,
) -> dict[str, Any]:
    schema = deepcopy(GEMINI_RESPONSE_SCHEMA)
    item_props = schema["properties"]["items"]["items"]["properties"]

    if allowed_meal_types:
        item_props["meal_type"]["format"] = "enum"
        item_props["meal_type"]["enum"] = [
            m.value if isinstance(m, MealType) else str(m) for m in allowed_meal_types
        ]

    if dish_ids:
        dish_enum = [str(id) for id in dish_ids]
        if dish_enum:
            item_props["dish_id"]["format"] = "enum"
            item_props["dish_id"]["enum"] = dish_enum

    return schema
