from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from nutrition.models import DishKind, DishRead, MenuRead, MenuStatus, ProductRead


class GeneratedMenuItem(BaseModel):
    slot: str
    dish_id: UUID | None = None
    product_id: UUID | None = None
    title: str
    portion_g: int | None = None
    portion_text: str | None = None
    kcal_est: int
    position: int = 1

    @model_validator(mode="after")
    def validate_reference(self) -> "GeneratedMenuItem":
        if (self.dish_id is None and self.product_id is None) or (
            self.dish_id is not None and self.product_id is not None
        ):
            raise ValueError("Exactly one of dish_id or product_id must be provided")
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
                        "description": "Назва прийому їжі (Сніданок, Обід, Вечеря, Перекус)",
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
                        "nullable": True,
                        "description": "Вага порції у грамах",
                    },
                    "portion_text": {
                        "type": "STRING",
                        "nullable": True,
                        "description": "Текстовий опис порції (наприклад '200 г' чи '1 шт')",
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
                "required": ["slot", "title", "kcal_est", "position"],
            },
        },
    },
    "required": ["rationale", "shopping_list", "items"],
}


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
    # Track dish portions already consumed in eaten meals
    dish_portions_used: dict[UUID, int] = {}
    product_portions_used: dict[UUID, int] = {}

    for menu in existing_menus:
        if menu.status == MenuStatus.EATEN:
            for item in menu.items:
                if item.dish_id and item.portion_g:
                    dish_portions_used[item.dish_id] = (
                        dish_portions_used.get(item.dish_id, 0) + item.portion_g
                    )
                if item.product_id and item.portion_g:
                    product_portions_used[item.product_id] = (
                        product_portions_used.get(item.product_id, 0) + item.portion_g
                    )

    # Filter items: if target_slot is set, consider only items for this slot
    relevant_items = (
        [it for it in response.items if it.slot == target_slot]
        if target_slot
        else response.items
    )

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
