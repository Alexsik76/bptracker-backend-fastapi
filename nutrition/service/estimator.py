import json
import logging
import time
from collections.abc import Iterable
from datetime import date
from typing import Any
from uuid import UUID

from pydantic import BaseModel, field_validator, model_validator
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

import gemini_infra
from config import Settings
from gemini_infra.types import GeminiInvalidResponse, GeminiUnavailable
from nutrition import crud
from nutrition.models import (
    DayMenuRead,
    MealType,
    Menu,
    MenuItem,
    MenuNoteRead,
    NutritionEstimate,
    NutritionSource,
    OffPlanEntryRead,
)
from nutrition.service.generator import (
    ServiceGeminiUnavailable,
    ServiceGenerationInvalidResponse,
    log_llm_exchange,
)

logger = logging.getLogger(__name__)

ESTIMATE_PROMPT_VERSION = "2026-10-27.4"
PRODUCT_ESTIMATE_PROMPT_VERSION = "2026-10-27.1"


class ItemNutritionEstimate(BaseModel):
    id: UUID
    kcal: float
    protein_g: float
    fat_g: float
    carbs_g: float
    fiber_g: float | None = None
    sugar_g: float | None = None
    sodium_mg: float | None = None

    @model_validator(mode="before")
    @classmethod
    def handle_legacy_keys(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "kcal" not in data and "kcal_est" in data:
                data["kcal"] = data["kcal_est"]
            if "protein_g" not in data:
                data["protein_g"] = 0.0
            if "fat_g" not in data:
                data["fat_g"] = 0.0
            if "carbs_g" not in data:
                data["carbs_g"] = 0.0
        return data

    @field_validator("kcal", "protein_g", "fat_g", "carbs_g", mode="before")
    @classmethod
    def validate_required(cls, v: Any) -> float:
        if v is None:
            raise ValueError("Required nutrition field cannot be null")
        val = float(v)
        if val < 0:
            raise ValueError("Nutrition values must be non-negative")
        return val

    @field_validator("fiber_g", "sugar_g", "sodium_mg", mode="before")
    @classmethod
    def validate_optional(cls, v: Any) -> float | None:
        if v is None:
            return None
        val = float(v)
        if val < 0:
            raise ValueError("Nutrition values must be non-negative")
        return val


class EstimateResponse(BaseModel):
    items: list[ItemNutritionEstimate]


PRODUCT_ESTIMATE_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "kcal": {"type": "NUMBER", "description": "Калорійність на 100 г (ккал, число >= 0)"},
        "protein_g": {"type": "NUMBER", "description": "Білки на 100 г (г, число >= 0)"},
        "fat_g": {"type": "NUMBER", "description": "Жири на 100 г (г, число >= 0)"},
        "carbs_g": {"type": "NUMBER", "description": "Вуглеводи на 100 г (г, число >= 0)"},
        "fiber_g": {
            "type": "NUMBER",
            "nullable": True,
            "description": "Клітковина на 100 г (г, або null)",
        },
        "sugar_g": {
            "type": "NUMBER",
            "nullable": True,
            "description": "Цукор на 100 г (г, або null)",
        },
        "sodium_mg": {
            "type": "NUMBER",
            "nullable": True,
            "description": "Натрій на 100 г (мг, або null)",
        },
    },
    "required": ["kcal", "protein_g", "fat_g", "carbs_g"],
}


def build_product_estimate_prompt(product_name: str) -> str:
    return (
        "Ти — експертний нутриціолог. Твоє завдання — оцінити харчову цінність на 100 г "
        f"для продукту: '{product_name}'.\n"
        "Визнач реалістичні значення для:\n"
        "- kcal: калорійність на 100 г (ккал, число >= 0);\n"
        "- protein_g: білки на 100 г (г, число >= 0);\n"
        "- fat_g: жири на 100 г (г, число >= 0);\n"
        "- carbs_g: вуглеводи на 100 г (г, число >= 0);\n"
        "- fiber_g: клітковина на 100 г (г, або null якщо невідомо);\n"
        "- sugar_g: цукор на 100 г (г, або null якщо невідомо);\n"
        "- sodium_mg: натрій на 100 г (мг, або null якщо невідомо).\n"
        "Обов'язково вкажи kcal, protein_g, fat_g, carbs_g. "
        "Пропущені значення мають бути null, не 0."
    )


async def estimate_product_nutrition(
    session: AsyncSession,
    user_id: UUID,
    product_name: str,
    settings: Settings,
    *,
    raise_on_error: bool = False,
) -> NutritionEstimate | None:
    """Estimate product nutrition per 100g with Gemini."""
    prompt = build_product_estimate_prompt(product_name)
    start_time = time.perf_counter()
    raw_response_text: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None

    try:
        gemini_result = await gemini_infra.generate_json(
            prompt=prompt,
            model=settings.gemini_menu_model,
            settings=settings,
            response_schema=PRODUCT_ESTIMATE_RESPONSE_SCHEMA,
        )
        duration_ms = int(round((time.perf_counter() - start_time) * 1000))
        raw_response_text = gemini_result.raw_text
        input_tokens = gemini_result.input_tokens
        output_tokens = gemini_result.output_tokens
        log_llm_exchange(
            settings=settings,
            user_id=user_id,
            menu_date=date.today(),
            scope="product_estimate",
            model=settings.gemini_menu_model,
            prompt_version=PRODUCT_ESTIMATE_PROMPT_VERSION,
            duration_ms=duration_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            prompt=prompt,
            raw_response=raw_response_text,
        )
        await crud.add_generation_log(
            session=session,
            user_id=user_id,
            menu_date=date.today(),
            scope="product_estimate",
            model=settings.gemini_menu_model,
            prompt_version=PRODUCT_ESTIMATE_PROMPT_VERSION,
            prompt=prompt,
            duration_ms=duration_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            raw_response=raw_response_text,
        )
        return NutritionEstimate.model_validate(gemini_result.data)
    except Exception as exc:
        duration_ms = int(round((time.perf_counter() - start_time) * 1000))
        logger.warning("Product estimation failed for '%s': %s", product_name, exc)
        log_llm_exchange(
            settings=settings,
            user_id=user_id,
            menu_date=date.today(),
            scope="product_estimate",
            model=settings.gemini_menu_model,
            prompt_version=PRODUCT_ESTIMATE_PROMPT_VERSION,
            duration_ms=duration_ms,
            input_tokens=None,
            output_tokens=None,
            prompt=prompt,
            raw_response=raw_response_text,
        )
        if raise_on_error:
            if isinstance(exc, GeminiUnavailable):
                raise ServiceGeminiUnavailable("Gemini service is unavailable") from exc
            raise ServiceGenerationInvalidResponse(f"Product estimation failed: {exc}") from exc
        return None


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
                        "kcal": {
                            "type": "NUMBER",
                            "description": "Оцінка калорійності у ккал (число >= 0)",
                        },
                        "protein_g": {
                            "type": "NUMBER",
                            "description": "Білки у грамах (число >= 0)",
                        },
                        "fat_g": {
                            "type": "NUMBER",
                            "description": "Жири у грамах (число >= 0)",
                        },
                        "carbs_g": {
                            "type": "NUMBER",
                            "description": "Вуглеводи у грамах (число >= 0)",
                        },
                        "fiber_g": {
                            "type": "NUMBER",
                            "nullable": True,
                            "description": "Клітковина у грамах (або null)",
                        },
                        "sugar_g": {
                            "type": "NUMBER",
                            "nullable": True,
                            "description": "Цукор у грамах (або null)",
                        },
                        "sodium_mg": {
                            "type": "NUMBER",
                            "nullable": True,
                            "description": "Натрій у мг (або null)",
                        },
                    },
                    "required": ["id", "kcal", "protein_g", "fat_g", "carbs_g"],
                },
                "description": "Оцінки харчової цінності для вільних пунктів меню",
            },
        },
        "required": ["items"],
    }


def build_estimate_prompt(items: list[MenuItem]) -> str:
    items_data = []
    for item in items:
        entry: dict[str, Any] = {
            "id": str(item.id),
            "title": item.title,
            "portion_g": item.portion_g,
            "portion_text": item.portion_text,
        }
        items_data.append(entry)

    items_json = json.dumps(items_data, ensure_ascii=False, indent=2)
    return (
        "Ти — експертний нутриціолог. Твоє завдання — оцінити харчову цінність "
        "(калорійність та макронутрієнти) для наданих пунктів меню.\n"
        "Для кожного пункту визнач реалістичні значення:\n"
        "- 'kcal': калорійність (ккал, число >= 0);\n"
        "- 'protein_g': білки (г, число >= 0);\n"
        "- 'fat_g': жири (г, число >= 0);\n"
        "- 'carbs_g': вуглеводи (г, число >= 0);\n"
        "- 'fiber_g': клітковина (г, або null якщо невідомо);\n"
        "- 'sugar_g': цукор (г, або null якщо невідомо);\n"
        "- 'sodium_mg': натрій (мг, або null якщо невідомо).\n"
        "Враховуй назву та розмір порції (portion_g / portion_text).\n\n"
        f"Пункти меню для оцінки:\n{items_json}\n\n"
        "Поверни JSON за схемою з полем 'items'. "
        "Обов'язково поверни оцінку для кожного вказаного пункту меню."
    )


async def estimate_day_kcal(
    session: AsyncSession,
    user_id: UUID,
    menu_date: date,
    settings: Settings,
    meal_type: MealType | None = None,
) -> DayMenuRead:
    """Estimate full nutrition set for free-text menu items missing kcal using Gemini."""
    stmt = (
        select(MenuItem)
        .join(Menu, col(MenuItem.menu_id) == col(Menu.id))
        .where(
            Menu.user_id == user_id,
            Menu.date == menu_date,
            Menu.eaten_at.is_(None),
            MenuItem.dish_id.is_(None),
            MenuItem.kcal.is_(None),
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

    prompt = build_estimate_prompt(items_to_estimate)

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

    estimates_by_id = {it.id: it for it in parsed_response.items}
    for item in items_to_estimate:
        est = estimates_by_id[item.id]
        item.kcal = est.kcal
        item.protein_g = est.protein_g
        item.fat_g = est.fat_g
        item.carbs_g = est.carbs_g
        item.fiber_g = est.fiber_g
        item.sugar_g = est.sugar_g
        item.sodium_mg = est.sodium_mg
        item.nutrition_source = NutritionSource.MODEL
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
    day_created = await crud.is_day_created(session, user_id, menu_date)

    return DayMenuRead(
        date=menu_date,
        day_created=day_created,
        menus=day_menus,
        notes=[MenuNoteRead.model_validate(n) for n in notes],
        off_plan=[OffPlanEntryRead.model_validate(e) for e in off_plan],
        available_pins=available_pins,
    )
