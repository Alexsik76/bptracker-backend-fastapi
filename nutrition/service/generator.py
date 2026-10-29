import logging
import time
from collections import defaultdict
from datetime import date, datetime
from uuid import UUID

from pydantic import ValidationError
from sqlmodel.ext.asyncio.session import AsyncSession

import gemini_infra
from app_settings import ai_config, nutrition_config
from config import Settings
from gemini_infra.types import GeminiInvalidResponse, GeminiUnavailable
from nutrition import crud
from nutrition.models import (
    GenerationResultRead,
    GenerationScope,
    MealType,
    MenuItemCreate,
    is_meal_generatable,
)
from nutrition.service.context import build_generation_context
from nutrition.service.prompt import PROMPT_VERSION, build_generation_prompt
from nutrition.service.schema import (
    GenerationResponse,
    build_gemini_response_schema,
    validate_generation_response,
)

logger = logging.getLogger(__name__)
llm_logger = logging.getLogger("nutrition.llm")


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
    """Client request parameters are invalid (e.g. missing slot in slot scope)."""


class ProfileNotFoundError(GenerationError):
    """Nutrition profile not found."""


class NothingToGenerateError(GenerationError):
    """No generatable meals found for generation scope."""


class ServiceGeminiUnavailable(GenerationError):
    """Gemini API is unavailable or timed out."""


class ServiceGenerationInvalidResponse(GenerationError):
    """Gemini returned invalid or schema-violating output."""

    def __init__(self, message: str, raw_response: str | None = None) -> None:
        super().__init__(message)
        self.raw_response = raw_response


async def generate_menu(
    session: AsyncSession,
    user_id: UUID,
    menu_date: date,
    scope: GenerationScope,
    meal_type: MealType | None,
    settings: Settings,
    now: datetime | None = None,
) -> GenerationResultRead:
    """Generate or update nutrition menu propositions for the specified scope."""
    if not await crud.is_day_created(session, user_id, menu_date):
        raise crud.DayNotCreatedError(f"Day {menu_date} not created")

    if scope == GenerationScope.SLOT and not meal_type:
        raise InvalidGenerationRequest("Meal type is required when scope is 'slot'")

    # 1. Pre-check profile
    profile = await crud.get_nutrition_profile(session, user_id)
    if profile is None:
        raise ProfileNotFoundError(
            "Nutrition profile not found. Please create a nutrition profile first."
        )

    # 2. Materialize pinned meals and pre-check canvas meals
    from nutrition.service.pinned import materialize_pinned_meals

    await materialize_pinned_meals(session, user_id, menu_date)

    canvas_menus = await crud.get_day_menus(session, user_id, menu_date, now=now)
    allowed_meal_types = [m.meal_type for m in canvas_menus]
    generatable_meals = [m for m in canvas_menus if is_meal_generatable(m)]

    if scope == GenerationScope.SLOT:
        if meal_type not in allowed_meal_types:
            raise InvalidGenerationRequest(
                f"Meal type '{meal_type.value if meal_type else None}' "
                "is not on the canvas or has been removed"
            )
        target_meal = next(m for m in canvas_menus if m.meal_type == meal_type)
        if not is_meal_generatable(target_meal):
            raise NothingToGenerateError(
                f"Meal '{meal_type.value if meal_type else None}' "
                "is not generatable (locked, missed, or already eaten)"
            )
    elif scope in (GenerationScope.DAY, GenerationScope.UNMARKED):
        if not generatable_meals:
            raise NothingToGenerateError(
                "No generatable meals on the canvas"
            )

    # Stage 1: Estimate kcal for all items in the day lacking kcal
    from nutrition.service.estimator import estimate_day_kcal

    await estimate_day_kcal(
        session=session,
        user_id=user_id,
        menu_date=menu_date,
        settings=settings,
        meal_type=None,
    )

    # Stage 2: Build context using CRUD with updated kcal
    context = await build_generation_context(
        session=session,
        user_id=user_id,
        menu_date=menu_date,
        scope=scope.value,
        target_meal_type=meal_type,
        history_days=nutrition_config.menu_history_days(),
        now=now,
    )

    # Build prompt
    prompt = build_generation_prompt(context)

    # End read transaction before calling Gemini so the connection is not idle in transaction
    await session.commit()

    # Call Gemini (outside open transaction)

    raw_response_text: str | None
    input_tokens: int | None = None
    output_tokens: int | None = None
    duration_ms: int | None = None

    start_time = time.perf_counter()
    try:
        gemini_result = await gemini_infra.generate_json(
            prompt=prompt,
            model=ai_config.menu_model(),
            settings=settings,
            response_schema=build_gemini_response_schema(
                context.dishes_map.keys(),
                context.products_map.keys(),
                allowed_meal_types=allowed_meal_types,
            ),
        )
        duration_ms = int(round((time.perf_counter() - start_time) * 1000))
        raw_response_text = gemini_result.raw_text
        input_tokens = gemini_result.input_tokens
        output_tokens = gemini_result.output_tokens
        log_llm_exchange(
            settings=settings,
            user_id=user_id,
            menu_date=menu_date,
            scope=scope.value,
            model=ai_config.menu_model(),
            prompt_version=PROMPT_VERSION,
            duration_ms=duration_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            prompt=prompt,
            raw_response=raw_response_text,
        )
    except GeminiUnavailable as exc:
        duration_ms = int(round((time.perf_counter() - start_time) * 1000))
        logger.error("Gemini unavailable during menu generation for user %s: %s", user_id, exc)
        log_llm_exchange(
            settings=settings,
            user_id=user_id,
            menu_date=menu_date,
            scope=scope.value,
            model=ai_config.menu_model(),
            prompt_version=PROMPT_VERSION,
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
            scope=scope.value,
            model=ai_config.menu_model(),
            prompt_version=PROMPT_VERSION,
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
            scope=scope.value,
            model=ai_config.menu_model(),
            prompt_version=PROMPT_VERSION,
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
            scope=scope.value,
            model=ai_config.menu_model(),
            prompt_version=PROMPT_VERSION,
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

    # 4. Parse & validate response
    try:
        parsed_response = GenerationResponse.model_validate(gemini_result.data)
        validate_generation_response(
            response=parsed_response,
            dishes_map=context.dishes_map,
            products_map=context.products_map,
            existing_menus=context.existing_day_menus,
            target_meal_type=meal_type if scope == GenerationScope.SLOT else None,
            allowed_meal_types=allowed_meal_types,
        )
    except (ValidationError, ValueError) as exc:
        logger.warning("Generation response schema validation failed for user %s: %s", user_id, exc)
        await crud.add_generation_log(
            session=session,
            user_id=user_id,
            menu_date=menu_date,
            scope=scope.value,
            model=ai_config.menu_model(),
            prompt_version=PROMPT_VERSION,
            prompt=prompt,
            duration_ms=duration_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            raw_response=raw_response_text,
        )
        await session.commit()
        raise ServiceGenerationInvalidResponse(
            f"Generation response validation failed: {exc}", raw_response=raw_response_text
        ) from exc

    # 5. Group generated items by meal type and apply in a single transaction via CRUD
    items_by_meal_type: dict[MealType, list[MenuItemCreate]] = defaultdict(list)
    for item_data in parsed_response.items:
        if scope == GenerationScope.SLOT and meal_type and item_data.meal_type != meal_type:
            continue
        items_by_meal_type[item_data.meal_type].append(
            MenuItemCreate(
                dish_id=item_data.dish_id,
                title=item_data.title,
                portion_g=item_data.portion_g,
                portion_text=item_data.portion_text,
                position=item_data.position,
            )
        )

    # Existing meals for today
    existing_menus_by_type = {m.meal_type: m for m in context.existing_day_menus}

    for target_meal_type, new_items in items_by_meal_type.items():
        if target_meal_type not in allowed_meal_types:
            continue
        existing_menu_read = existing_menus_by_type.get(target_meal_type)
        # If meal is not generatable (e.g. all items eaten/skipped), skip it entirely
        if existing_menu_read and not is_meal_generatable(existing_menu_read):
            continue

        menu_entity = await crud.get_or_create_menu(
            session=session,
            user_id=user_id,
            menu_date=menu_date,
            meal_type=target_meal_type,
        )
        await crud.replace_unmarked_slot_items(
            session=session,
            menu=menu_entity,
            new_items_data=new_items,
        )

    # Write generation log via CRUD
    await crud.add_generation_log(
        session=session,
        user_id=user_id,
        menu_date=menu_date,
        scope=scope.value,
        model=ai_config.menu_model(),
        prompt_version=PROMPT_VERSION,
        prompt=prompt,
        duration_ms=duration_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        raw_response=raw_response_text,
    )

    # Commit all changes atomically
    await session.commit()

    # Re-read day's menus
    day_menus = await crud.get_day_menus(session, user_id, menu_date, now=now)

    return GenerationResultRead(
        menus=day_menus,
        rationale=parsed_response.rationale,
        shopping_list=parsed_response.shopping_list,
    )
