import json
import logging
import time
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from app_settings import ai_config
from config import Settings
from db import async_session_factory
from nutrition import crud
from nutrition.models.enums import Sex
from nutrition.models.menu import MenuRead
from nutrition.models.profile import NutritionProfile
from typesafe_infra import (
    JevInvalidResponse,
    JevUnavailable,
    evaluate_systemone,
)

logger = logging.getLogger(__name__)
llm_logger = logging.getLogger("nutrition.llm")


def calculate_user_goal_data(
    profile: NutritionProfile,
    target_date: date | None = None,
) -> dict[str, Any]:
    """Calculate user goal direction and daily kcal norm from profile."""
    weight = float(profile.weight_kg)
    target_weight = float(profile.target_weight_kg)
    pace = float(profile.pace_kg_per_week) if profile.pace_kg_per_week else 0.5

    if target_weight < weight:
        goal_direction = "дефіцит"
    elif target_weight > weight:
        goal_direction = "профіцит"
    else:
        goal_direction = "утримання"

    ref_year = target_date.year if target_date else datetime.now(UTC).year
    age = max(1, ref_year - profile.birth_year)

    if profile.sex == Sex.MALE:
        bmr = 10.0 * weight + 6.25 * float(profile.height_cm) - 5.0 * age + 5.0
    else:
        bmr = 10.0 * weight + 6.25 * float(profile.height_cm) - 5.0 * age - 161.0

    tdee = bmr * 1.2
    daily_delta = (pace * 7700.0) / 7.0

    if goal_direction == "дефіцит":
        daily_kcal_norm = max(1200.0, tdee - daily_delta)
    elif goal_direction == "профіцит":
        daily_kcal_norm = tdee + daily_delta
    else:
        daily_kcal_norm = tdee

    return {
        "goal_direction": goal_direction,
        "daily_kcal_norm": round(daily_kcal_norm, 1),
    }


def build_jev_state(
    profile: NutritionProfile,
    day_menus: list[MenuRead],
    target_date: date,
) -> dict[str, Any]:
    """Build the state object sent to JEV."""
    user_goal = calculate_user_goal_data(profile, target_date)

    meals_data = []
    day_total_kcal = 0.0
    for m in day_menus:
        items_data = []
        for it in m.items:
            portion_str = it.portion_text or (f"{it.portion_g} г" if it.portion_g else "")
            items_data.append({
                "name": it.title,
                "portion_text": portion_str,
                "kcal": it.kcal,
            })
            if it.kcal is not None:
                day_total_kcal += it.kcal

        meals_data.append({
            "type": m.meal_type.value,
            "time": m.time,
            "items": items_data,
        })

    return {
        "user_goal": user_goal,
        "day_menu": {
            "meals": meals_data,
            "day_total_kcal": round(day_total_kcal, 1),
            "total_kcal": round(day_total_kcal, 1),
        },
    }


async def evaluate_menu_day(
    session: AsyncSession,
    user_id: UUID,
    menu_date: date,
    settings: Settings,
) -> None:
    """Evaluate day menu against active criteria using TypeSafe JEV."""
    if not settings.typesafe_api_key:
        logger.info("Evaluation skipped: typesafe_api_key is not configured")
        return

    criteria = await crud.get_active_evaluation_criteria(session)
    if not criteria:
        logger.info("Evaluation skipped: no active criteria in DB")
        return

    profile = await crud.get_nutrition_profile(session, user_id)
    if not profile:
        logger.info("Evaluation skipped: user %s has no nutrition profile", user_id)
        return

    day_menus = await crud.get_day_menus(session, user_id, menu_date)
    state = build_jev_state(profile, day_menus, menu_date)
    questions = {
        c.key: {
            "type": "choice",
            "instructions": c.instructions,
            "criteria": c.criteria,
        }
        for c in criteria
    }

    model = ai_config.jev_model()
    criteria_version = criteria[0].version
    payload = {
        "model": model,
        "state": state,
        "questions": questions,
    }
    prompt_json = json.dumps(payload, ensure_ascii=False)
    start_time = time.perf_counter()

    try:
        result = await evaluate_systemone(
            state=state,
            questions=questions,
            settings=settings,
            model=model,
        )
    except JevInvalidResponse as exc:
        duration_ms = int(round((time.perf_counter() - start_time) * 1000))
        await crud.add_generation_log(
            session=session,
            user_id=user_id,
            menu_date=menu_date,
            scope="menu_evaluation",
            model=model,
            prompt_version=str(criteria_version),
            prompt=prompt_json,
            duration_ms=duration_ms,
            input_tokens=None,
            output_tokens=None,
            raw_response=exc.raw_text,
        )
        logger.error(
            "JEV invalid response for user %s on %s: %s",
            user_id,
            menu_date,
            exc,
        )
        return
    except JevUnavailable as exc:
        duration_ms = int(round((time.perf_counter() - start_time) * 1000))
        await crud.add_generation_log(
            session=session,
            user_id=user_id,
            menu_date=menu_date,
            scope="menu_evaluation",
            model=model,
            prompt_version=str(criteria_version),
            prompt=prompt_json,
            duration_ms=duration_ms,
            input_tokens=None,
            output_tokens=None,
            raw_response=None,
        )
        logger.error(
            "JEV unavailable for user %s on %s: %s",
            user_id,
            menu_date,
            exc,
        )
        return

    duration_ms = int(round((time.perf_counter() - start_time) * 1000))
    input_tokens = result.usage.input_tokens if result.usage else None
    output_tokens = result.usage.output_tokens if result.usage else None

    if ai_config.llm_exchange_log():
        llm_logger.info(
            "user_id=%s date=%s scope=menu_evaluation model=%s version=%s duration_ms=%s "
            "tokens=(input=%s, output=%s)\nPROMPT:\n%s\nRAW RESPONSE:\n%s",
            user_id,
            menu_date,
            result.model,
            criteria_version,
            duration_ms,
            input_tokens,
            output_tokens,
            prompt_json,
            result.raw_response,
        )

    await crud.add_generation_log(
        session=session,
        user_id=user_id,
        menu_date=menu_date,
        scope="menu_evaluation",
        model=result.model,
        prompt_version=str(criteria_version),
        prompt=prompt_json,
        duration_ms=duration_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        raw_response=result.raw_response,
    )

    await crud.save_menu_day_evaluations(
        session=session,
        user_id=user_id,
        menu_date=menu_date,
        criteria_version=criteria_version,
        answers=result.answers,
        model=result.model,
    )


async def evaluate_menu_day_background(
    user_id: UUID,
    menu_date: date,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    """Background task wrapper: evaluates day menu with its own session and catches all errors."""
    factory = session_factory if session_factory is not None else async_session_factory
    try:
        async with factory() as session:
            await evaluate_menu_day(
                session=session,
                user_id=user_id,
                menu_date=menu_date,
                settings=settings,
            )
            await session.commit()
    except Exception as exc:
        logger.exception(
            "Failed background evaluation for user %s on date %s: %s",
            user_id,
            menu_date,
            exc,
        )
