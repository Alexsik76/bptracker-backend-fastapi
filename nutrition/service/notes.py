import json
import logging
import time
from datetime import date
from typing import Any
from uuid import UUID

from sqlmodel.ext.asyncio.session import AsyncSession

import gemini_infra
from config import Settings
from gemini_infra.types import GeminiInvalidResponse, GeminiUnavailable
from nutrition import crud
from nutrition.models import (
    GenerationScope,
    MenuNoteCreate,
    MenuNoteKind,
    MenuNoteRead,
    NutritionProfileRead,
)
from nutrition.service.generator import (
    ProfileNotFoundError,
    ServiceGeminiUnavailable,
    ServiceGenerationInvalidResponse,
    generate_menu,
    log_llm_exchange,
)

INFO_NOTE_PROMPT_VERSION = "2026-10-26.1"


class InvalidNoteRequest(Exception):
    """Note request validation error (e.g. empty text, unsupported note kind)."""

logger = logging.getLogger(__name__)

INFO_NOTE_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "answer": {
            "type": "STRING",
            "description": (
                "Конкретна та лаконічна відповідь на запитання користувача щодо харчування"
            ),
        },
    },
    "required": ["answer"],
}


def build_info_note_prompt(
    profile: NutritionProfileRead,
    existing_day_menus: list[Any],
    menu_date: date,
    question: str,
    off_plan_entries: list[Any] | None = None,
) -> str:
    profile_data = {
        "sex": profile.sex.value if profile.sex else "unknown",
        "birth_year": profile.birth_year,
        "height_cm": profile.height_cm,
        "weight_kg": float(profile.weight_kg) if profile.weight_kg else None,
        "target_weight_kg": (
            float(profile.target_weight_kg) if profile.target_weight_kg else None
        ),
        "pace_kg_per_week": (
            float(profile.pace_kg_per_week) if profile.pace_kg_per_week else 0.5
        ),
        "restrictions": profile.restrictions,
        "dislikes": profile.dislikes,
    }

    meals_summary = []
    for m in existing_day_menus:
        meals_summary.append(
            {
                "slot": m.slot,
                "status": m.status.value,
                "items": [
                    {
                        "title": it.title,
                        "status": it.status.value if it.status else None,
                        "portion_g": it.portion_g,
                        "portion_text": it.portion_text,
                        "kcal_est": it.kcal_est,
                    }
                    for it in m.items
                ],
            }
        )

    off_plan_block = ""
    if off_plan_entries:
        off_plan_data = [
            {
                "at": o.at.isoformat() if hasattr(o.at, "isoformat") else str(o.at),
                "text": o.text,
                "kcal_est": o.kcal_est,
            }
            for o in off_plan_entries
        ]
        off_plan_block = (
            f"\nПОЗАПЛАНОВЕ СПОЖИВАННЯ ({menu_date}):\n"
            f"{json.dumps(off_plan_data, ensure_ascii=False, indent=2)}\n"
        )

    return f"""Ти — професійний дієтолог і консультант із щоденного харчування.
Дай чітку, практичну, безпечну та лаконічну відповідь на запитання користувача
щодо харчування на день {menu_date}.

ПРОФІЛЬ КОРИСТУВАЧА:
{json.dumps(profile_data, ensure_ascii=False, indent=2)}

РАЦІОН НА ЦЕЙ ДЕНЬ ({menu_date}):
{json.dumps(meals_summary, ensure_ascii=False, indent=2)}
{off_plan_block}
ЗАПИТАННЯ КОРИСТУВАЧА:
"{question}"

Поверни JSON з обов'язковим текстовим полем "answer".
Відповідь має бути прямою, без зайвої «води», з урахуванням профілю здоров'я
та медичних обмежень користувача (наприклад, допустима вага порції,
глікемічний індекс, баланс жирів чи калорій).
"""


async def answer_info_note(
    session: AsyncSession,
    user_id: UUID,
    menu_date: date,
    text: str,
    settings: Settings,
) -> MenuNoteRead:
    profile = await crud.get_nutrition_profile(session, user_id)
    if profile is None:
        raise ProfileNotFoundError(
            "Nutrition profile not found. Please create a nutrition profile first."
        )

    profile_read = NutritionProfileRead.model_validate(profile)
    existing_menus = await crud.get_day_menus(session, user_id, menu_date)
    off_plan_rows = await crud.get_off_plan_entries(session, user_id, menu_date)
    prompt = build_info_note_prompt(
        profile_read, existing_menus, menu_date, text, off_plan_entries=off_plan_rows
    )

    # End read transaction before calling Gemini so the connection is not idle in transaction
    await session.commit()

    duration_ms: int | None = None
    start_time = time.perf_counter()
    try:
        gemini_result = await gemini_infra.generate_json(
            prompt=prompt,
            model=settings.gemini_menu_model,
            settings=settings,
            response_schema=INFO_NOTE_RESPONSE_SCHEMA,
        )
        duration_ms = int(round((time.perf_counter() - start_time) * 1000))
        log_llm_exchange(
            settings=settings,
            user_id=user_id,
            menu_date=menu_date,
            scope="info",
            model=settings.gemini_menu_model,
            prompt_version=INFO_NOTE_PROMPT_VERSION,
            duration_ms=duration_ms,
            input_tokens=gemini_result.input_tokens,
            output_tokens=gemini_result.output_tokens,
            prompt=prompt,
            raw_response=gemini_result.raw_text,
        )
    except GeminiUnavailable as exc:
        duration_ms = int(round((time.perf_counter() - start_time) * 1000))
        logger.error("Gemini unavailable during info note for user %s: %s", user_id, exc)
        log_llm_exchange(
            settings=settings,
            user_id=user_id,
            menu_date=menu_date,
            scope="info",
            model=settings.gemini_menu_model,
            prompt_version=INFO_NOTE_PROMPT_VERSION,
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
            scope="info",
            model=settings.gemini_menu_model,
            prompt_version=INFO_NOTE_PROMPT_VERSION,
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
        log_llm_exchange(
            settings=settings,
            user_id=user_id,
            menu_date=menu_date,
            scope="info",
            model=settings.gemini_menu_model,
            prompt_version=INFO_NOTE_PROMPT_VERSION,
            duration_ms=duration_ms,
            input_tokens=None,
            output_tokens=None,
            prompt=prompt,
            raw_response=exc.raw_text,
        )
        await crud.add_generation_log(
            session=session,
            user_id=user_id,
            menu_date=menu_date,
            scope="info",
            model=settings.gemini_menu_model,
            prompt_version=INFO_NOTE_PROMPT_VERSION,
            prompt=prompt,
            duration_ms=duration_ms,
            input_tokens=None,
            output_tokens=None,
            raw_response=exc.raw_text,
        )
        await session.commit()
        raise ServiceGenerationInvalidResponse(
            f"Invalid Gemini response: {exc}", raw_response=exc.raw_text
        ) from exc

    answer = None
    if isinstance(gemini_result.data, dict):
        answer = gemini_result.data.get("answer")

    if not answer or not isinstance(answer, str):
        await crud.add_generation_log(
            session=session,
            user_id=user_id,
            menu_date=menu_date,
            scope="info",
            model=settings.gemini_menu_model,
            prompt_version=INFO_NOTE_PROMPT_VERSION,
            prompt=prompt,
            duration_ms=duration_ms,
            input_tokens=gemini_result.input_tokens,
            output_tokens=gemini_result.output_tokens,
            raw_response=gemini_result.raw_text,
        )
        await session.commit()
        raise ServiceGenerationInvalidResponse(
            "Gemini response missing valid 'answer' string",
            raw_response=gemini_result.raw_text,
        )

    await crud.add_generation_log(
        session=session,
        user_id=user_id,
        menu_date=menu_date,
        scope="info",
        model=settings.gemini_menu_model,
        prompt_version=INFO_NOTE_PROMPT_VERSION,
        prompt=prompt,
        duration_ms=duration_ms,
        input_tokens=gemini_result.input_tokens,
        output_tokens=gemini_result.output_tokens,
        raw_response=gemini_result.raw_text,
    )

    note = await crud.create_menu_note(
        session=session,
        user_id=user_id,
        menu_date=menu_date,
        kind=MenuNoteKind.INFO,
        text=text,
        answer=answer,
    )
    await session.commit()
    return MenuNoteRead.model_validate(note)


async def handle_correction_note(
    session: AsyncSession,
    user_id: UUID,
    menu_date: date,
    text: str,
    settings: Settings,
) -> MenuNoteRead:
    profile = await crud.get_nutrition_profile(session, user_id)
    if profile is None:
        raise ProfileNotFoundError(
            "Nutrition profile not found. Please create a nutrition profile first."
        )

    # 1. Save correction note into DB and commit so it persists even if generation fails
    note = await crud.create_menu_note(
        session=session,
        user_id=user_id,
        menu_date=menu_date,
        kind=MenuNoteKind.CORRECTION,
        text=text,
        answer=None,
    )
    await session.commit()
    await session.refresh(note)

    # 2. Trigger generation of unmarked positions
    await generate_menu(
        session=session,
        user_id=user_id,
        menu_date=menu_date,
        scope=GenerationScope.UNMARKED,
        slot=None,
        settings=settings,
    )

    return MenuNoteRead.model_validate(note)


async def process_menu_note(
    session: AsyncSession,
    user_id: UUID,
    menu_date: date,
    data: MenuNoteCreate,
    settings: Settings,
) -> MenuNoteRead:
    text = data.text.strip()
    if not text:
        raise InvalidNoteRequest("Note text cannot be empty")

    if data.kind == MenuNoteKind.INFO:
        return await answer_info_note(
            session=session,
            user_id=user_id,
            menu_date=menu_date,
            text=text,
            settings=settings,
        )
    elif data.kind == MenuNoteKind.CORRECTION:
        return await handle_correction_note(
            session=session,
            user_id=user_id,
            menu_date=menu_date,
            text=text,
            settings=settings,
        )
    raise InvalidNoteRequest(f"Unsupported note kind: {data.kind}")
