import json
import logging
import time
from collections.abc import Iterable
from datetime import date
from typing import Any
from uuid import UUID

from pydantic import BaseModel, field_validator
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

import gemini_infra
from config import Settings
from gemini_infra.types import GeminiInvalidResponse, GeminiUnavailable
from nutrition import crud
from nutrition.models import (
    DayMenuRead,
    KcalSource,
    MealType,
    Menu,
    MenuItem,
    MenuNoteRead,
    OffPlanEntryRead,
)
from nutrition.service.generator import (
    ServiceGeminiUnavailable,
    ServiceGenerationInvalidResponse,
    log_llm_exchange,
)

logger = logging.getLogger(__name__)

ESTIMATE_PROMPT_VERSION = "2026-10-27.2"


class ItemEstimate(BaseModel):
    id: UUID
    kcal_est: int

    @field_validator("kcal_est")
    @classmethod
    def validate_kcal(cls, v: Any) -> int:
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError("kcal_est must be an integer")
        if v < 0:
            raise ValueError("kcal_est must be non-negative")
        return v


class EstimateResponse(BaseModel):
    items: list[ItemEstimate]


def build_estimate_response_schema(item_ids: Iterable[UUID]) -> dict[str, Any]:
    id_list = [str(i) for i in item_ids]
    return {
        "type": "OBJECT",
        "properties": {
            "items": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "id": {
                            "type": "STRING",
                            "format": "enum",
                            "enum": id_list,
                            "description": "ID пункту меню",
                        },
                        "kcal_est": {
                            "type": "INTEGER",
                            "description": "Оцінка калорійності у ккал (ціле число >= 0)",
                        },
                    },
                    "required": ["id", "kcal_est"],
                },
                "description": "Оцінки калорійності для пунктів меню",
            },
        },
        "required": ["items"],
    }


def build_estimate_prompt(
    items: list[MenuItem],
    dishes_map: dict[UUID, Any],
    products_map: dict[UUID, Any],
) -> str:
    items_data = []
    for item in items:
        entry: dict[str, Any] = {
            "id": str(item.id),
            "title": item.title,
            "portion_g": item.portion_g,
            "portion_text": item.portion_text,
        }
        if item.dish_id and item.dish_id in dishes_map:
            dish = dishes_map[item.dish_id]
            dish_info: dict[str, Any] = {
                "name": dish.name,
                "kind": dish.kind.value if hasattr(dish.kind, "value") else str(dish.kind),
            }
            if dish.cooked_weight_g is not None:
                dish_info["cooked_weight_g"] = dish.cooked_weight_g
            if hasattr(dish, "components") and dish.components:
                dish_info["components"] = [
                    {
                        "name": c.name,
                        "raw_weight_g": c.raw_weight_g,
                        "cooked_weight_g": c.cooked_weight_g,
                    }
                    for c in dish.components
                ]
            if dish.notes:
                dish_info["notes"] = dish.notes
            entry["dish"] = dish_info
        elif item.product_id and item.product_id in products_map:
            product = products_map[item.product_id]
            entry["product"] = {
                "name": product.name,
            }
        items_data.append(entry)

    items_json = json.dumps(items_data, ensure_ascii=False, indent=2)
    return (
        "Ти — експертний нутриціолог. Твоє завдання — оцінити калорійність (ккал) "
        "для наданих пунктів меню.\n"
        "Для кожного пункту визнач реалістичну кількість кілокалорій (ціле невід'ємне число >= 0) "
        "з урахуванням розміру порції (portion_g / portion_text), назви та даних страви "
        "або продукту (якщо вони надані).\n\n"
        f"Пункти меню для оцінки:\n{items_json}\n\n"
        "Поверни JSON за схемою з полем 'items', що містить список об'єктів з полями:\n"
        "- 'id': ідентифікатор пункту меню (рядок UUID зі списку вище);\n"
        "- 'kcal_est': оцінка калорійності в ккал (ціле невід'ємне число >= 0).\n"
        "Обов'язково поверни оцінку для кожного вказаного пункту меню."
    )


async def estimate_day_kcal(
    session: AsyncSession,
    user_id: UUID,
    menu_date: date,
    settings: Settings,
    meal_type: MealType | None = None,
) -> DayMenuRead:
    """Estimate calories for menu items missing kcal_est using Gemini."""
    stmt = (
        select(MenuItem)
        .join(Menu, col(MenuItem.menu_id) == col(Menu.id))
        .where(
            Menu.user_id == user_id,
            Menu.date == menu_date,
            Menu.eaten_at.is_(None),
            MenuItem.kcal_est.is_(None),
        )
        .order_by(col(Menu.meal_type).asc(), col(MenuItem.position).asc())
    )
    if meal_type is not None:
        stmt = stmt.where(Menu.meal_type == meal_type)

    items_to_estimate = list((await session.exec(stmt)).all())

    if not items_to_estimate:
        day_menus = await crud.get_day_menus(session, user_id, menu_date)
        notes = await crud.get_menu_notes(session, user_id, menu_date)
        off_plan = await crud.get_off_plan_entries(session, user_id, menu_date)
        available_pins = await crud.get_available_pins(session, user_id, menu_date)
        return DayMenuRead(
            date=menu_date,
            menus=day_menus,
            notes=[MenuNoteRead.model_validate(n) for n in notes],
            off_plan=[OffPlanEntryRead.model_validate(e) for e in off_plan],
            available_pins=available_pins,
        )

    dish_ids = {item.dish_id for item in items_to_estimate if item.dish_id is not None}
    product_ids = {item.product_id for item in items_to_estimate if item.product_id is not None}

    dishes_map: dict[UUID, Any] = {}
    for d_id in dish_ids:
        d = await crud.get_dish(session, d_id, user_id)
        if d is not None:
            dishes_map[d_id] = d

    products_map: dict[UUID, Any] = {}
    for p_id in product_ids:
        p = await crud.get_product(session, p_id, user_id)
        if p is not None:
            products_map[p_id] = p

    prompt = build_estimate_prompt(items_to_estimate, dishes_map, products_map)

    # End read transaction before calling Gemini so the connection is not idle in transaction
    await session.commit()

    start_time = time.perf_counter()
    raw_response_text: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    duration_ms: int | None = None

    try:
        gemini_result = await gemini_infra.generate_json(
            prompt=prompt,
            model=settings.gemini_menu_model,
            settings=settings,
            response_schema=build_estimate_response_schema([it.id for it in items_to_estimate]),
        )
        duration_ms = int(round((time.perf_counter() - start_time) * 1000))
        raw_response_text = gemini_result.raw_text
        input_tokens = gemini_result.input_tokens
        output_tokens = gemini_result.output_tokens
        log_llm_exchange(
            settings=settings,
            user_id=user_id,
            menu_date=menu_date,
            scope="estimate",
            model=settings.gemini_menu_model,
            prompt_version=ESTIMATE_PROMPT_VERSION,
            duration_ms=duration_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            prompt=prompt,
            raw_response=raw_response_text,
        )
    except GeminiUnavailable as exc:
        duration_ms = int(round((time.perf_counter() - start_time) * 1000))
        logger.error("Gemini unavailable during kcal estimation for user %s: %s", user_id, exc)
        log_llm_exchange(
            settings=settings,
            user_id=user_id,
            menu_date=menu_date,
            scope="estimate",
            model=settings.gemini_menu_model,
            prompt_version=ESTIMATE_PROMPT_VERSION,
            duration_ms=duration_ms,
            input_tokens=None,
            output_tokens=None,
            prompt=prompt,
            raw_response=None,
        )
        await crud.add_generation_log(
            session=session,
            user_id=user_id,
            menu_date=menu_date,
            scope="estimate",
            model=settings.gemini_menu_model,
            prompt_version=ESTIMATE_PROMPT_VERSION,
            prompt=prompt,
            duration_ms=duration_ms,
            input_tokens=None,
            output_tokens=None,
            raw_response=None,
        )
        await session.commit()
        raise ServiceGeminiUnavailable("Gemini service is unavailable") from exc
    except GeminiInvalidResponse as exc:
        duration_ms = int(round((time.perf_counter() - start_time) * 1000))
        raw_response_text = exc.raw_text
        log_llm_exchange(
            settings=settings,
            user_id=user_id,
            menu_date=menu_date,
            scope="estimate",
            model=settings.gemini_menu_model,
            prompt_version=ESTIMATE_PROMPT_VERSION,
            duration_ms=duration_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            prompt=prompt,
            raw_response=raw_response_text,
        )
        await crud.add_generation_log(
            session=session,
            user_id=user_id,
            menu_date=menu_date,
            scope="estimate",
            model=settings.gemini_menu_model,
            prompt_version=ESTIMATE_PROMPT_VERSION,
            prompt=prompt,
            duration_ms=duration_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            raw_response=raw_response_text,
        )
        await session.commit()
        raise ServiceGenerationInvalidResponse(
            f"Invalid Gemini response: {exc}", raw_response=raw_response_text
        ) from exc

    try:
        parsed_response = EstimateResponse.model_validate(gemini_result.data)
        requested_ids = {it.id for it in items_to_estimate}
        returned_ids = [it.id for it in parsed_response.items]

        if len(returned_ids) != len(set(returned_ids)):
            raise ValueError("Duplicate item ids in response")

        if set(returned_ids) != requested_ids:
            raise ValueError(
                f"Returned item ids do not match requested set. "
                f"Expected: {requested_ids}, got: {set(returned_ids)}"
            )
    except Exception as exc:
        logger.warning("Estimation response validation failed for user %s: %s", user_id, exc)
        await crud.add_generation_log(
            session=session,
            user_id=user_id,
            menu_date=menu_date,
            scope="estimate",
            model=settings.gemini_menu_model,
            prompt_version=ESTIMATE_PROMPT_VERSION,
            prompt=prompt,
            duration_ms=duration_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            raw_response=raw_response_text,
        )
        await session.commit()
        raise ServiceGenerationInvalidResponse(
            f"Estimation response validation failed: {exc}",
            raw_response=raw_response_text,
        ) from exc

    estimates_by_id = {it.id: it.kcal_est for it in parsed_response.items}
    for item in items_to_estimate:
        item.kcal_est = estimates_by_id[item.id]
        item.kcal_source = KcalSource.MODEL
        session.add(item)

    await crud.add_generation_log(
        session=session,
        user_id=user_id,
        menu_date=menu_date,
        scope="estimate",
        model=settings.gemini_menu_model,
        prompt_version=ESTIMATE_PROMPT_VERSION,
        prompt=prompt,
        duration_ms=duration_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        raw_response=raw_response_text,
    )
    await session.commit()

    day_menus = await crud.get_day_menus(session, user_id, menu_date)
    notes = await crud.get_menu_notes(session, user_id, menu_date)
    off_plan = await crud.get_off_plan_entries(session, user_id, menu_date)
    available_pins = await crud.get_available_pins(session, user_id, menu_date)

    return DayMenuRead(
        date=menu_date,
        menus=day_menus,
        notes=[MenuNoteRead.model_validate(n) for n in notes],
        off_plan=[OffPlanEntryRead.model_validate(e) for e in off_plan],
        available_pins=available_pins,
    )
