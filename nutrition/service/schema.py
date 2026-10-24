import re
from collections.abc import Iterable
from copy import deepcopy
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from nutrition.models import DishKind, DishRead, MenuRead, MenuStatus, ProductRead


class GeneratedMenuItem(BaseModel):
    slot: str
    dish_id: UUID | None = None
    product_id: UUID | None = None
    title: str
    portion_g: int = Field(gt=0)
    portion_text: str | None = None
    kcal_est: int = Field(ge=0)
    position: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_reference(self) -> "GeneratedMenuItem":
        self.slot = re.sub(r"\s*\(\d{1,2}:\d{2}\)\s*$", "", self.slot).strip()
        if not self.slot:
            raise ValueError("Slot name must not be empty")
        if (self.dish_id is None and self.product_id is None) or (
            self.dish_id is not None and self.product_id is not None
        ):
            raise ValueError("Exactly one of dish_id or product_id must be provided")
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
                    "slot": {
                        "type": "STRING",
                        "description": (
                            "Точна назва прийому їжі без часу "
                            "(наприклад 'Сніданок', 'Обід', 'Вечеря', 'Перекус')"
                        ),
                    },
                    "dish_id": {
                        "type": "STRING",
                        "nullable": True,
                        "description": "UUID страви зі списку наявних страв або null",
                    },
                    "product_id": {
                        "type": "STRING",
                        "nullable": True,
                        "description": "UUID продукту зі списку наявних продуктів або null",
                    },
                    "title": {
                        "type": "STRING",
                        "description": "Назва позиції (знімок назви страви або продукту)",
                    },
                    "portion_g": {
                        "type": "INTEGER",
                        "description": "Вага порції у грамах (обов'язково додатне число)",
                    },
                    "portion_text": {
                        "type": "STRING",
                        "description": (
                            "Текстовий опис порції (наприклад '200 г' чи '1 шт (150 г)')"
                        ),
                    },
                    "kcal_est": {
                        "type": "INTEGER",
                        "description": "Оцінка калорійності цієї позиції у ккал",
                    },
                    "position": {
                        "type": "INTEGER",
                        "description": "Порядковий номер позиції в межах прийому їжі (1, 2, ...)",
                    },
                },
                "required": [
                    "slot",
                    "dish_id",
                    "product_id",
                    "title",
                    "portion_g",
                    "portion_text",
                    "kcal_est",
                    "position",
                ],
            },
        },
    },
    "required": ["rationale", "shopping_list", "items"],
}


def build_gemini_response_schema(
    dish_ids: Iterable[UUID],
    product_ids: Iterable[UUID],
) -> dict[str, Any]:
    schema = deepcopy(GEMINI_RESPONSE_SCHEMA)
    item_props = schema["properties"]["items"]["items"]["properties"]

    dish_enum = [str(id) for id in dish_ids]
    if dish_enum:
        item_props["dish_id"]["format"] = "enum"
        item_props["dish_id"]["enum"] = dish_enum

    product_enum = [str(id) for id in product_ids]
    if product_enum:
        item_props["product_id"]["format"] = "enum"
        item_props["product_id"]["enum"] = product_enum

    return schema


def validate_generation_response(
    response: GenerationResponse,
    dishes_map: dict[UUID, DishRead],
    products_map: dict[UUID, ProductRead],
    existing_menus: list[MenuRead],
    target_slot: str | None = None,
) -> None:
    """Validate that generated items reference available entities and obey limits.

    Raises ValueError on validation failure.
    """
    # Ready dish remaining_g in DB already has EATEN meals deducted (via crud.update_menu).
    # Product daily_limit_g is a daily cap, so EATEN meals still count toward product_portions_used.
    dish_portions_used: dict[UUID, int] = {}
    product_portions_used: dict[UUID, int] = {}
    locked_slots: set[str] = set()

    for menu in existing_menus:
        if menu.status in (MenuStatus.EATEN, MenuStatus.SKIPPED):
            locked_slots.add(menu.slot)
            if menu.status == MenuStatus.EATEN:
                for item in menu.items:
                    if item.product_id and item.portion_g:
                        product_portions_used[item.product_id] = (
                            product_portions_used.get(item.product_id, 0) + item.portion_g
                        )
        elif target_slot and menu.slot != target_slot:
            # Regenerating a single slot: other planned slots on the same day keep reservations
            for item in menu.items:
                if item.dish_id and item.portion_g:
                    dish_portions_used[item.dish_id] = (
                        dish_portions_used.get(item.dish_id, 0) + item.portion_g
                    )
                if item.product_id and item.portion_g:
                    product_portions_used[item.product_id] = (
                        product_portions_used.get(item.product_id, 0) + item.portion_g
                    )

    # Ignore items for locked (eaten/skipped) slots since generator never overwrites them
    if target_slot:
        relevant_items = [
            it for it in response.items if it.slot == target_slot and it.slot not in locked_slots
        ]
        if target_slot not in locked_slots and not relevant_items:
            raise ValueError(f"No items generated for target slot '{target_slot}'")
    else:
        relevant_items = [it for it in response.items if it.slot not in locked_slots]

    for item in relevant_items:
        if item.dish_id is not None:
            dish = dishes_map.get(item.dish_id)
            if dish is None:
                raise ValueError(f"Referenced dish {item.dish_id} does not exist or not available")
            if dish.kind == DishKind.READY:
                portion = item.portion_g or 0
                new_total = dish_portions_used.get(dish.id, 0) + portion
                if dish.remaining_g is not None and new_total > dish.remaining_g:
                    err = (
                        f"Cumulative portion {new_total}g for ready dish '{dish.name}' "
                        f"exceeds available remaining {dish.remaining_g}g"
                    )
                    raise ValueError(err)
                dish_portions_used[dish.id] = new_total

        elif item.product_id is not None:
            product = products_map.get(item.product_id)
            if product is None:
                raise ValueError(
                    f"Referenced product {item.product_id} does not exist or not available"
                )
            if product.daily_limit_g is not None:
                portion = item.portion_g or 0
                total_used = product_portions_used.get(product.id, 0) + portion
                if total_used > product.daily_limit_g:
                    err = (
                        f"Portion for product '{product.name}' "
                        f"exceeds daily limit {product.daily_limit_g}g"
                    )
                    raise ValueError(err)
                product_portions_used[product.id] = total_used
