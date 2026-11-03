import json
import logging
import time
from collections.abc import Iterable
from datetime import date
from typing import Any
from uuid import UUID

from pydantic import BaseModel, field_validator, model_validator
from sqlmodel.ext.asyncio.session import AsyncSession

import gemini_infra
from app_settings import ai_config
from config import Settings
from gemini_infra.types import GeminiInvalidResponse, GeminiUnavailable
from nutrition import crud
from nutrition.clock import utc_today
from nutrition.models import (
    MenuItem,
    NutritionEstimate,
    NutritionSource,
)
from nutrition.service.generator import (
    ServiceGeminiUnavailable,
    ServiceGenerationInvalidResponse,
    log_llm_exchange,
)

logger = logging.getLogger(__name__)

ESTIMATE_PROMPT_VERSION = "2026-10-27.4"
DISH_ESTIMATE_PROMPT_VERSION = "2026-10-27.1"
PRODUCT_ESTIMATE_PROMPT_VERSION = DISH_ESTIMATE_PROMPT_VERSION


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


DISH_ESTIMATE_RESPONSE_SCHEMA = {
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

PRODUCT_ESTIMATE_RESPONSE_SCHEMA = DISH_ESTIMATE_RESPONSE_SCHEMA


def build_dish_estimate_prompt(dish_name: str) -> str:
    return (
        "Ти — експертний нутриціолог. Твоє завдання — оцінити харчову цінність на 100 г "
        f"для страви/продукту: '{dish_name}'.\n"
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


build_product_estimate_prompt = build_dish_estimate_prompt


async def estimate_dish_nutrition(
    session: AsyncSession,
    user_id: UUID,
    dish_name: str,
    settings: Settings,
    *,
    raise_on_error: bool = False,
) -> NutritionEstimate | None:
    """Estimate dish nutrition per 100g with Gemini."""
    prompt = build_dish_estimate_prompt(dish_name)
    start_time = time.perf_counter()
    raw_response_text: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None

    try:
        gemini_result = await gemini_infra.generate_json(
            prompt=prompt,
            model=ai_config.menu_model(),
            settings=settings,
            response_schema=DISH_ESTIMATE_RESPONSE_SCHEMA,
        )
        duration_ms = int(round((time.perf_counter() - start_time) * 1000))
        raw_response_text = gemini_result.raw_text
        input_tokens = gemini_result.input_tokens
        output_tokens = gemini_result.output_tokens
        log_llm_exchange(
            settings=settings,
            user_id=user_id,
            menu_date=utc_today(),
            scope="dish_estimate",
            model=ai_config.menu_model(),
            prompt_version=DISH_ESTIMATE_PROMPT_VERSION,
            duration_ms=duration_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            prompt=prompt,
            raw_response=raw_response_text,
        )
        await crud.add_generation_log(
            session=session,
            user_id=user_id,
            menu_date=utc_today(),
            scope="dish_estimate",
            model=ai_config.menu_model(),
            prompt_version=DISH_ESTIMATE_PROMPT_VERSION,
            prompt=prompt,
            duration_ms=duration_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            raw_response=raw_response_text,
        )
        return NutritionEstimate.model_validate(gemini_result.data)
    except Exception as exc:
        duration_ms = int(round((time.perf_counter() - start_time) * 1000))
        logger.warning("Dish estimation failed for '%s': %s", dish_name, exc)
        log_llm_exchange(
            settings=settings,
            user_id=user_id,
            menu_date=utc_today(),
            scope="dish_estimate",
            model=ai_config.menu_model(),
            prompt_version=DISH_ESTIMATE_PROMPT_VERSION,
            duration_ms=duration_ms,
            input_tokens=None,
            output_tokens=None,
            prompt=prompt,
            raw_response=raw_response_text,
        )
        if raise_on_error:
            if isinstance(exc, GeminiUnavailable):
                raise ServiceGeminiUnavailable("Gemini service is unavailable") from exc
            raise ServiceGenerationInvalidResponse(f"Dish estimation failed: {exc}") from exc
        return None


estimate_product_nutrition = estimate_dish_nutrition


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


async def estimate_items_nutrition(
    session: AsyncSession,
    user_id: UUID,
    items: list[MenuItem],
    settings: Settings,
    menu_date: date | None = None,
) -> list[MenuItem]:
    """Estimate full nutrition set for given free-text menu items missing kcal."""
    if not items:
        return []

    if menu_date is None:
        menu_date = utc_today()

    prompt = build_estimate_prompt(items)
    await session.commit()

    start_time = time.perf_counter()
    raw_response_text: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    duration_ms: int | None = None

    try:
        gemini_result = await gemini_infra.generate_json(
            prompt=prompt,
            model=ai_config.menu_model(),
            settings=settings,
            response_schema=build_estimate_response_schema([i.id for i in items if i.id]),
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
            model=ai_config.menu_model(),
            prompt_version=ESTIMATE_PROMPT_VERSION,
            duration_ms=duration_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            prompt=prompt,
            raw_response=raw_response_text,
        )
        parsed = EstimateResponse.model_validate(gemini_result.data)
    except GeminiUnavailable as exc:
        duration_ms = int(round((time.perf_counter() - start_time) * 1000))
        log_llm_exchange(
            settings=settings,
            user_id=user_id,
            menu_date=menu_date,
            scope="estimate",
            model=ai_config.menu_model(),
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
            model=ai_config.menu_model(),
            prompt_version=ESTIMATE_PROMPT_VERSION,
            prompt=prompt,
            duration_ms=duration_ms,
            input_tokens=None,
            output_tokens=None,
            raw_response=None,
        )
        await session.commit()
        raise ServiceGeminiUnavailable("Gemini service is unavailable") from exc
    except (GeminiInvalidResponse, Exception) as exc:
        duration_ms = int(round((time.perf_counter() - start_time) * 1000))
        log_llm_exchange(
            settings=settings,
            user_id=user_id,
            menu_date=menu_date,
            scope="estimate",
            model=ai_config.menu_model(),
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
            model=ai_config.menu_model(),
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

    # Apply estimates
    estimates_by_id = {est.id: est for est in parsed.items}
    for item in items:
        est = estimates_by_id.get(item.id)
        if est:
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
        model=ai_config.menu_model(),
        prompt_version=ESTIMATE_PROMPT_VERSION,
        prompt=prompt,
        duration_ms=duration_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        raw_response=raw_response_text,
    )
    await session.commit()
    return items
