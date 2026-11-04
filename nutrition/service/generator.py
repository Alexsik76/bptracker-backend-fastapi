import json
import logging
import re
import time
from datetime import UTC, date, datetime
from typing import Any, Literal
from uuid import UUID

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

import gemini_infra
from app_settings import ai_config
from config import Settings
from gemini_infra.types import GeminiInvalidResponse, GeminiUnavailable
from nutrition import crud
from nutrition.crud.menu import get_menu_items_for_menu, to_menu_item_read, to_menu_read
from nutrition.models import (
    DishRead,
    MealType,
    Menu,
    MenuItem,
    MenuItemOrigin,
    MenuItemRead,
    MenuOrigin,
    MenuRead,
    MenuState,
    NutritionProfile,
    NutritionSource,
    ProductRead,
)
from nutrition.service.evaluator import calculate_daily_norms

logger = logging.getLogger(__name__)
llm_logger = logging.getLogger("nutrition.llm")

SUGGESTION_PROMPT_VERSION = "2026-11-03.2"


def log_llm_exchange(
    settings: Settings,
    user_id: UUID,
    menu_date: date,
    scope: str,
    model: str,
    prompt_version: str,
    duration_ms: int | None,
    input_tokens: int | None,
    output_tokens: int | None,
    prompt: str,
    raw_response: str | None,
) -> None:
    """Emit full LLM prompt and response exchange record to nutrition.llm at INFO."""
    if not ai_config.llm_exchange_log():
        return
    llm_logger.info(
        "user_id=%s date=%s scope=%s model=%s prompt_version=%s duration_ms=%s "
        "tokens=(input=%s, output=%s)\nPROMPT:\n%s\nRAW RESPONSE:\n%s",
        user_id,
        menu_date,
        scope,
        model,
        prompt_version,
        duration_ms,
        input_tokens,
        output_tokens,
        prompt,
        raw_response,
    )


class GenerationError(Exception):
    """Base exception for generation service errors."""


class InvalidGenerationRequest(GenerationError):
    """Client request parameters are invalid."""


class ProfileNotFoundError(GenerationError):
    """Nutrition profile not found."""


class NothingToGenerateError(GenerationError):
    """No generatable items found."""


class ServiceGeminiUnavailable(GenerationError):
    """Gemini API is unavailable or timed out."""


class ServiceGenerationInvalidResponse(GenerationError):
    """Gemini returned invalid or schema-violating output."""

    def __init__(self, message: str, raw_response: str | None = None) -> None:
        super().__init__(message)
        self.raw_response = raw_response


SUGGESTION_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "meal_type": {
            "type": "STRING",
            "enum": [m.value for m in MealType],
            "description": "Meal type: breakfast, lunch, dinner, snack",
        },
        "items": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "dish_id": {
                        "type": "STRING",
                        "nullable": True,
                        "description": "UUID of matched catalogue dish, or null",
                    },
                    "name": {
                        "type": "STRING",
                        "description": "Dish title or name",
                    },
                    "grams": {
                        "type": "INTEGER",
                        "description": "Portion weight in grams",
                    },
                    "kcal": {
                        "type": "NUMBER",
                        "description": "Portion calories (kcal, >= 0)",
                    },
                    "protein_g": {
                        "type": "NUMBER",
                        "description": "Portion protein (g, >= 0)",
                    },
                    "fat_g": {
                        "type": "NUMBER",
                        "description": "Portion fat (g, >= 0)",
                    },
                    "carbs_g": {
                        "type": "NUMBER",
                        "description": "Portion carbohydrates (g, >= 0)",
                    },
                },
                "required": ["name", "grams", "kcal", "protein_g", "fat_g", "carbs_g"],
            },
            "description": "Meal items",
        },
    },
    "required": ["meal_type", "items"],
}


def extract_time_from_text(text: str | None) -> tuple[int, int] | None:
    """Extract hour and minute (0-23, 0-59) from user text, or None if no time named."""
    if not text:
        return None
    match_colon = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)(?::[0-5]\d)?\b", text)
    if match_colon:
        return int(match_colon.group(1)), int(match_colon.group(2))
    match_prep = re.search(
        r"\b(?:о|в|at)\s+([01]?\d|2[0-3])(?:[:.]([0-5]\d))?(?:\s*(?:год(?:ин[іа]?)?))?(?:\s*(ранку|дня|вечора|ночі))?\b",
        text,
        re.IGNORECASE,
    )
    if match_prep:
        hour = int(match_prep.group(1))
        minute = int(match_prep.group(2)) if match_prep.group(2) else 0
        period = match_prep.group(3).lower() if match_prep.group(3) else None
        if period in ("вечора", "дня") and hour < 12:
            hour += 12
        return hour, minute
    return None


def build_suggestion_prompt(
    profile: NutritionProfile | None,
    kcal_norm: float | None,
    protein_norm: float | None,
    available_products: list[ProductRead],
    ready_and_pinned_dishes: list[DishRead],
    other_dishes: list[DishRead],
    day_journal_meals: list[MenuRead],
    at: datetime,
    target_date: date,
    text: str | None,
    mode: Literal["suggest", "log"] = "suggest",
) -> str:
    # 1. Profile information
    if profile:
        profile_lines = [
            f"Стать: {profile.sex.value}, рік народження: {profile.birth_year}",
            f"Зріст: {profile.height_cm} см, поточна вага: {profile.weight_kg} кг",
            f"Цільова вага: {profile.target_weight_kg} кг",
            f"Темп: {profile.pace_kg_per_week} кг/тиждень",
            f"Харчові обмеження: {profile.restrictions or 'немає'}",
            f"Не подобається / неприйнятне: {profile.dislikes or 'немає'}",
            (
                f"Ваги генерації (від 0.0 до 1.0): різноманітність={profile.weight_variety}, "
                f"збалансованість={profile.weight_balance}, "
                f"відповідність меті={profile.weight_goal}"
            ),
        ]
    else:
        profile_lines = [
            "Профіль не створено.",
            "Ваги генерації: різноманітність=0.5, збалансованість=0.5, відповідність меті=0.5",
        ]
    profile_text = "\n".join(profile_lines)

    # 2. Norms and day totals
    day_kcal = 0.0
    day_protein = 0.0
    day_fat = 0.0
    day_carbs = 0.0
    journal_lines = []
    for m in day_journal_meals:
        m_items_str = []
        for it in m.items:
            portion = it.portion_text or f"{it.portion_g} г"
            it_desc = (
                f"{it.title} ({portion}, {it.kcal or 0} ккал, "
                f"Б:{it.protein_g or 0}г, Ж:{it.fat_g or 0}г, В:{it.carbs_g or 0}г)"
            )
            m_items_str.append(it_desc)
            if it.kcal is not None:
                day_kcal += it.kcal
            if it.protein_g is not None:
                day_protein += it.protein_g
            if it.fat_g is not None:
                day_fat += it.fat_g
            if it.carbs_g is not None:
                day_carbs += it.carbs_g
        time_str = m.eaten_at.strftime("%H:%M") if m.eaten_at else "постійна страва"
        journal_lines.append(f"- {m.meal_type.value} ({time_str}): " + "; ".join(m_items_str))

    day_kcal = round(day_kcal, 1)
    day_protein = round(day_protein, 1)
    day_fat = round(day_fat, 1)
    day_carbs = round(day_carbs, 1)

    budget_lines = [
        f"Денна норма калорій: {kcal_norm} ккал" if kcal_norm else "Норма калорій: не задана",
        f"Денна норма білка: {protein_norm} г" if protein_norm else "Норма білка: не задана",
        (
            f"З'їдено ({target_date.isoformat()}): {day_kcal} ккал, "
            f"білок {day_protein} г, жири {day_fat} г, вуглеводи {day_carbs} г"
        ),
    ]
    if kcal_norm is not None:
        budget_lines.append(f"Залишок калорій на день: {round(kcal_norm - day_kcal, 1)} ккал")
    if protein_norm is not None:
        budget_lines.append(f"Залишок білка на день: {round(protein_norm - day_protein, 1)} г")
    budget_text = "\n".join(budget_lines)

    if journal_lines:
        eaten_meals_text = "\n".join(journal_lines)
    else:
        eaten_meals_text = "Сьогодні ще нічого не було з'їдено."

    # 3. Available products
    if available_products:
        prod_items = [
            f"- {p.name}" + (f" ({p.note})" if p.note else "") for p in available_products
        ]
        products_text = "\n".join(prod_items)
    else:
        products_text = "Немає відмічених наявних продуктів."

    # 4. Ready and pinned dishes first, then other catalogue dishes
    def format_dish(d: DishRead) -> dict[str, Any]:
        info: dict[str, Any] = {
            "id": str(d.id),
            "name": d.name,
            "per_100g": {
                "kcal": d.kcal,
                "protein_g": d.protein_g,
                "fat_g": d.fat_g,
                "carbs_g": d.carbs_g,
            },
        }
        if d.is_ready:
            info["is_ready"] = True
        if d.pinned_meal_type:
            info["pinned_meal_type"] = d.pinned_meal_type.value
        if d.note:
            info["note"] = d.note
        return info

    ready_pinned_json = json.dumps(
        [format_dish(d) for d in ready_and_pinned_dishes], ensure_ascii=False, indent=2
    )
    other_dishes_json = json.dumps(
        [format_dish(d) for d in other_dishes], ensure_ascii=False, indent=2
    )

    user_text_display = (
        text.strip() if text and text.strip() else "Побажання відсутні (порожній запит)"
    )

    if mode == "suggest":
        intro_task = (
            "Твоє завдання — проаналізувати контекст користувача, поточний час та його побажання і "
            "запропонувати прийом їжі у вигляді JSON-відповіді."
        )
        rules_text = (
            "--- ПРАВИЛА РОБОТИ ---\n"
            "1. Запропонуй один прийом їжі (meal_type) для поточного часу на основі контексту "
            "та побажань користувача. Тільки пропонуй прийом їжі і ніколи не записуй нічого як "
            "з'їдене (навіть якщо текст користувача звучить як спожите).\n"
            "2. Прийом повинен складатись із 2-4 окремих страв (items) з конкретними "
            "грамами порцій.\n"
            "3. Пріоритет вибору страв:\n"
            "   1) Готові страви (is_ready=true) та закріплені страви (pinned_meal_type).\n"
            "   2) Страви, які можна легко приготувати з наявних вдома продуктів.\n"
            "   3) Інші страви з каталогу або нові прості страви.\n"
            "4. Суворо враховуй обмеження профілю (restrictions) та небажані продукти (dislikes).\n"
            "5. Враховуй залишок калорій і білка на сьогодні — не перевищуй денний бюджет.\n"
            "6. Ваги профілю керують балансом вибору:\n"
            "   * weight_variety (різноманітність): уникай повторення продуктів, пропонуй нове.\n"
            "   * weight_balance (збалансованість): дбай про співвідношення БЖВ, "
            "клітковину/овочі.\n"
            "   * weight_goal (відповідність меті): суворо підганяй калораж та білок під норму.\n"
            "7. Для кожної страви поверни: dish_id або null (якщо страва є в каталозі — вкажи "
            "її dish_id), name, grams, та розраховані КБЖВ на цю порцію "
            "(kcal, protein_g, fat_g, carbs_g).\n\n"
            "Поверни відповідь виключно у форматі JSON відповідно до наданої схеми."
        )
    else:
        intro_task = (
            "Твоє завдання — розібрати текст користувача про вже з'їдену їжу "
            "(страви/продукти з грамами) і сформувати запис прийому їжі у вигляді JSON-відповіді."
        )
        rules_text = (
            "--- ПРАВИЛА РОБОТИ ---\n"
            "1. Текст користувача описує їжу, яку він вже з'їв. Твоє завдання — тільки розібрати "
            "спожите (страви/продукти з грамами) у запис прийому їжі.\n"
            "2. Визнач відповідний прийом їжі (meal_type: breakfast, lunch, dinner, snack).\n"
            "3. Сформуй прийом їжі зі спожитих страв (items). Якщо страва є в каталозі, вкажи "
            "її dish_id; для решти вкажи назву (name).\n"
            "4. Для кожної страви визнач грами (grams) та КБЖВ на порцію "
            "(kcal, protein_g, fat_g, carbs_g).\n\n"
            "Поверни відповідь виключно у форматі JSON відповідно до наданої схеми."
        )

    return (
        "Ти — експертний нутриціолог у застосунку 'Що їсти?'.\n"
        f"{intro_task}\n\n"
        f"--- ПОТОЧНИЙ ЧАС ТА ЗАПИТ ---\n"
        f"Дата клієнта: {target_date.isoformat()}\n"
        f"Момент клієнта (at): {at.isoformat()} ({at.strftime('%H:%M')})\n"
        f"Текст користувача: {user_text_display}\n\n"
        f"--- ПРОФІЛЬ КОРИСТУВАЧА ---\n"
        f"{profile_text}\n\n"
        f"--- НОРМИ ТА ДЕННИЙ БЮДЖЕТ ---\n"
        f"{budget_text}\n\n"
        f"--- ЖУРНАЛ ДНЯ (З'ЇДЕНЕ СЬОГОДНІ) ---\n"
        f"{eaten_meals_text}\n\n"
        f"--- ПРОДУКТИ ВДОМА (available=true) ---\n"
        f"{products_text}\n\n"
        f"--- ГОТОВІ ТА ЗАКРІПЛЕНІ СТРАВИ (перший пріоритет) ---\n"
        f"{ready_pinned_json}\n\n"
        f"--- РЕШТА СТРАВ З КАТАЛОГУ ---\n"
        f"{other_dishes_json}\n\n"
        f"{rules_text}"
    )


async def generate_suggestion(
    session: AsyncSession,
    user_id: UUID,
    target_date: date,
    at: datetime,
    text: str | None,
    settings: Settings,
    mode: Literal["suggest", "log"] = "suggest",
) -> tuple[Literal["suggest", "log"], MenuRead]:
    # 1. Fetch profile and norms
    profile = await crud.get_nutrition_profile(session, user_id)
    kcal_norm, protein_norm = calculate_daily_norms(profile, target_date)

    # 2. Fetch products and dishes
    available_products = await crud.get_available_products(session, user_id)
    all_dishes = list(await crud.get_dishes(session, user_id))
    ready_and_pinned = [d for d in all_dishes if d.is_ready or d.pinned_meal_type is not None]
    other_dishes = [d for d in all_dishes if not (d.is_ready or d.pinned_meal_type is not None)]

    # 3. Fetch day journal meals (eaten meals for target_date)
    stmt_eaten = (
        select(Menu)
        .where(
            Menu.user_id == user_id,
            Menu.date == target_date,
            Menu.state == MenuState.EATEN,
        )
        .order_by(Menu.eaten_at.asc().nulls_last(), Menu.created_at.asc())
    )
    eaten_menu_entities = list((await session.exec(stmt_eaten)).all())
    eaten_meals: list[MenuRead] = []
    for m in eaten_menu_entities:
        items = await get_menu_items_for_menu(session, m.id)
        eaten_meals.append(to_menu_read(m, items))

    # 4. Build prompt
    prompt = build_suggestion_prompt(
        profile=profile,
        kcal_norm=kcal_norm,
        protein_norm=protein_norm,
        available_products=available_products,
        ready_and_pinned_dishes=ready_and_pinned,
        other_dishes=other_dishes,
        day_journal_meals=eaten_meals,
        at=at,
        target_date=target_date,
        text=text,
        mode=mode,
    )

    # 5. Call Gemini
    start_time = time.perf_counter()
    model_name = ai_config.menu_model()
    try:
        gemini_result = await gemini_infra.generate_json(
            prompt=prompt,
            model=model_name,
            settings=settings,
            response_schema=SUGGESTION_RESPONSE_SCHEMA,
        )
    except GeminiUnavailable as exc:
        duration_ms = int(round((time.perf_counter() - start_time) * 1000))
        log_llm_exchange(
            settings=settings,
            user_id=user_id,
            menu_date=target_date,
            scope="suggestion",
            model=model_name,
            prompt_version=SUGGESTION_PROMPT_VERSION,
            duration_ms=duration_ms,
            input_tokens=None,
            output_tokens=None,
            prompt=prompt,
            raw_response=None,
        )
        raise ServiceGeminiUnavailable(str(exc)) from exc
    except GeminiInvalidResponse as exc:
        duration_ms = int(round((time.perf_counter() - start_time) * 1000))
        log_llm_exchange(
            settings=settings,
            user_id=user_id,
            menu_date=target_date,
            scope="suggestion",
            model=model_name,
            prompt_version=SUGGESTION_PROMPT_VERSION,
            duration_ms=duration_ms,
            input_tokens=None,
            output_tokens=None,
            prompt=prompt,
            raw_response=exc.raw_text,
        )
        raise ServiceGenerationInvalidResponse(str(exc), raw_response=exc.raw_text) from exc

    duration_ms = int(round((time.perf_counter() - start_time) * 1000))
    data = gemini_result.data
    if not isinstance(data, dict):
        raise ServiceGenerationInvalidResponse(
            "Expected JSON object from model",
            raw_response=gemini_result.raw_text,
        )

    meal_type_str = data.get("meal_type", "lunch")
    try:
        meal_type = MealType(meal_type_str)
    except ValueError:
        meal_type = MealType.LUNCH

    items_data = data.get("items", [])
    if not isinstance(items_data, list) or not items_data:
        raise ServiceGenerationInvalidResponse(
            "Items list is missing or empty",
            raw_response=gemini_result.raw_text,
        )

    # 6. Apply mode logic
    # Dish lookup by id or name
    dish_by_id = {d.id: d for d in all_dishes}
    dish_by_name = {d.name.strip().lower(): d for d in all_dishes}

    if mode == "suggest":
        # A new suggestion replaces any previous suggestion for this day;
        # stale suggestions (date earlier than target_date) are also deleted.
        await crud.delete_stale_suggestions(session, user_id, target_date, commit=False)
        stmt_prev = select(Menu).where(
            Menu.user_id == user_id,
            Menu.state == MenuState.SUGGESTION,
            Menu.date == target_date,
        )
        prev_suggestions = list((await session.exec(stmt_prev)).all())
        for prev in prev_suggestions:
            await session.delete(prev)
        await session.flush()

        menu = Menu(
            user_id=user_id,
            date=target_date,
            meal_type=meal_type,
            origin=MenuOrigin.SUGGESTION,
            state=MenuState.SUGGESTION,
            eaten_at=None,
        )
        session.add(menu)
        await session.flush()
    else:
        # "log": records meal into request date. Eaten moment: at when date is today;
        # for a past date use midday of that date, unless the text names a time.
        if target_date == at.date():
            eaten_moment = at
        else:
            tz = at.tzinfo if at.tzinfo is not None else UTC
            parsed_time = extract_time_from_text(text)
            if parsed_time is not None:
                h, m = parsed_time
                eaten_moment = datetime(
                    target_date.year,
                    target_date.month,
                    target_date.day,
                    h,
                    m,
                    0,
                    tzinfo=tz,
                )
            else:
                eaten_moment = datetime(
                    target_date.year,
                    target_date.month,
                    target_date.day,
                    12,
                    0,
                    0,
                    tzinfo=tz,
                )

        menu = Menu(
            user_id=user_id,
            date=target_date,
            meal_type=meal_type,
            origin=MenuOrigin.MANUAL,
            state=MenuState.EATEN,
            eaten_at=eaten_moment,
        )
        session.add(menu)
        await session.flush()

    created_items: list[MenuItemRead] = []
    for idx, it in enumerate(items_data):
        dish_id_raw = it.get("dish_id")
        matched_dish = None
        if dish_id_raw:
            try:
                candidate_id = UUID(str(dish_id_raw))
                matched_dish = dish_by_id.get(candidate_id)
            except ValueError:
                matched_dish = None

        name_str = str(it.get("name", "")).strip()
        if matched_dish is None and name_str:
            matched_dish = dish_by_name.get(name_str.lower())

        resolved_dish_id = matched_dish.id if matched_dish else None
        title = name_str or (matched_dish.name if matched_dish else "Страва")
        grams = max(1, int(it.get("grams", 100)))

        kcal = max(0.0, float(it.get("kcal", 0.0)))
        protein_g = max(0.0, float(it.get("protein_g", 0.0)))
        fat_g = max(0.0, float(it.get("fat_g", 0.0)))
        carbs_g = max(0.0, float(it.get("carbs_g", 0.0)))

        is_gen = mode == "suggest"
        nutr_source = (
            NutritionSource.COMPUTED if resolved_dish_id else NutritionSource.MODEL
        )
        menu_item = MenuItem(
            menu_id=menu.id,
            dish_id=resolved_dish_id,
            title=title,
            portion_g=grams,
            portion_text=f"{grams} г",
            nutrition_source=nutr_source,
            origin=MenuItemOrigin.GENERATED if is_gen else MenuItemOrigin.MANUAL,
            position=idx,
            kcal=round(kcal, 1),
            protein_g=round(protein_g, 1),
            fat_g=round(fat_g, 1),
            carbs_g=round(carbs_g, 1),
        )
        session.add(menu_item)
        await session.flush()
        created_items.append(to_menu_item_read(menu_item))

    # 7. Generation log
    await crud.add_generation_log(
        session=session,
        user_id=user_id,
        menu_date=target_date,
        scope="suggestion",
        model=gemini_result.model,
        prompt_version=SUGGESTION_PROMPT_VERSION,
        prompt=prompt,
        duration_ms=duration_ms,
        input_tokens=gemini_result.input_tokens,
        output_tokens=gemini_result.output_tokens,
        raw_response=gemini_result.raw_text,
        menu_id=menu.id,
    )
    log_llm_exchange(
        settings=settings,
        user_id=user_id,
        menu_date=target_date,
        scope="suggestion",
        model=gemini_result.model,
        prompt_version=SUGGESTION_PROMPT_VERSION,
        duration_ms=duration_ms,
        input_tokens=gemini_result.input_tokens,
        output_tokens=gemini_result.output_tokens,
        prompt=prompt,
        raw_response=gemini_result.raw_text,
    )

    await session.commit()
    await session.refresh(menu)
    return mode, to_menu_read(menu, created_items)
