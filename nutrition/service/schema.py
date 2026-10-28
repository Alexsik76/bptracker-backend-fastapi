from collections.abc import Iterable
from copy import deepcopy
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from nutrition.models import (
    DishRead,
    MealType,
    MenuRead,
    ProductRead,
    is_meal_generatable,
)


class GeneratedMenuItem(BaseModel):
    meal_type: MealType
    dish_id: UUID
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
                    "dish_id",
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
    dish_ids: Iterable[UUID],
    product_ids: Iterable[UUID] | None = None,
    allowed_meal_types: Iterable[MealType] | None = None,
) -> dict[str, Any]:
    schema = deepcopy(GEMINI_RESPONSE_SCHEMA)
    item_props = schema["properties"]["items"]["items"]["properties"]

    if allowed_meal_types:
        item_props["meal_type"]["format"] = "enum"
        item_props["meal_type"]["enum"] = [
            m.value if isinstance(m, MealType) else str(m) for m in allowed_meal_types
        ]

    dish_enum = [str(id) for id in dish_ids]
    if dish_enum:
        item_props["dish_id"]["format"] = "enum"
        item_props["dish_id"]["enum"] = dish_enum

    return schema


def validate_generation_response(
    response: GenerationResponse,
    dishes_map: dict[UUID, DishRead],
    products_map: dict[UUID, ProductRead],
    existing_menus: list[MenuRead],
    target_meal_type: MealType | None = None,
    allowed_meal_types: Iterable[MealType] | None = None,
) -> None:
    """Validate that generated items reference available entities and obey limits.

    Raises ValueError on validation failure.
    """
    if allowed_meal_types:
        allowed_meal_types_set = {
            m if isinstance(m, MealType) else MealType(m) for m in allowed_meal_types
        }
        for item in response.items:
            if item.meal_type not in allowed_meal_types_set:
                allowed_vals = [m.value for m in sorted(allowed_meal_types_set)]
                raise ValueError(
                    f"Generated meal type '{item.meal_type.value}' is not allowed in profile "
                    f"schedule. Allowed meal types: {allowed_vals}"
                )

    dish_portions_used: dict[UUID, int] = {}
    locked_meal_types: set[MealType] = set()

    for menu in existing_menus:
        if not is_meal_generatable(menu):
            locked_meal_types.add(menu.meal_type)

        if target_meal_type and menu.meal_type != target_meal_type:
            # In meal scope, non-eaten meals of other types keep reserving portions
            if menu.eaten_at is None:
                for item in menu.items:
                    if item.dish_id and item.portion_g:
                        dish_portions_used[item.dish_id] = (
                            dish_portions_used.get(item.dish_id, 0) + item.portion_g
                        )

    # Ignore items for locked meal types since generator never overwrites them
    if target_meal_type:
        relevant_items = [
            it
            for it in response.items
            if it.meal_type == target_meal_type and it.meal_type not in locked_meal_types
        ]
        if target_meal_type not in locked_meal_types and not relevant_items:
            raise ValueError(
                f"No items generated for target meal type '{target_meal_type.value}'"
            )
    else:
        relevant_items = [
            it for it in response.items if it.meal_type not in locked_meal_types
        ]

    for item in relevant_items:
        dish = dishes_map.get(item.dish_id)
        if dish is None:
            raise ValueError(f"Referenced dish {item.dish_id} does not exist or not available")

        portion = item.portion_g or 0
        new_total = dish_portions_used.get(dish.id, 0) + portion
        if dish.remaining_g is not None and new_total > dish.remaining_g:
            err = (
                f"Cumulative portion {new_total}g for dish '{dish.name}' "
                f"exceeds available remaining {dish.remaining_g}g"
            )
            raise ValueError(err)
        dish_portions_used[dish.id] = new_total
