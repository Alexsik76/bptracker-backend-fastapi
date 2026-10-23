import logging
from collections import defaultdict
from datetime import date
from uuid import UUID

from pydantic import ValidationError
from sqlmodel.ext.asyncio.session import AsyncSession

import gemini_infra
from config import Settings
from gemini_infra.types import GeminiInvalidResponse, GeminiUnavailable
from nutrition import crud
from nutrition.models import (
    GenerationResultRead,
    GenerationScope,
    MenuItemCreate,
    MenuStatus,
)
from nutrition.service.context import build_generation_context
from nutrition.service.prompt import PROMPT_VERSION, build_generation_prompt
from nutrition.service.schema import (
    GEMINI_RESPONSE_SCHEMA,
    GenerationResponse,
    validate_generation_response,
)

logger = logging.getLogger(__name__)


class GenerationError(Exception):
    """Base exception for generation service errors."""


class ProfileNotFoundError(GenerationError):
    """Nutrition profile not found."""


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
    slot: str | None,
    settings: Settings,
) -> GenerationResultRead:
    """Generate or update nutrition menu propositions for the specified scope."""
    if scope == GenerationScope.SLOT and not slot:
        raise ValueError("Slot name is required when scope is 'slot'")

    # 1. Build context using CRUD
    context = await build_generation_context(
        session=session,
        user_id=user_id,
        menu_date=menu_date,
        scope=scope.value,
        target_slot=slot,
        history_days=settings.menu_history_days,
    )

    if context.profile is None:
        raise ProfileNotFoundError(
            "Nutrition profile not found. Please create a nutrition profile first."
        )

    # 2. Build prompt
    prompt = build_generation_prompt(context)

    # 3. Call Gemini (outside open transaction)
    raw_response_text: str | None
    input_tokens: int | None = None
    output_tokens: int | None = None

    try:
        gemini_result = await gemini_infra.generate_json(
            prompt=prompt,
            model=settings.gemini_menu_model,
            settings=settings,
            response_schema=GEMINI_RESPONSE_SCHEMA,
        )
        raw_response_text = gemini_result.raw_text
        input_tokens = gemini_result.input_tokens
        output_tokens = gemini_result.output_tokens
    except GeminiUnavailable as exc:
        logger.error("Gemini unavailable during menu generation for user %s: %s", user_id, exc)
        raise ServiceGeminiUnavailable("Gemini service is unavailable") from exc
    except GeminiInvalidResponse as exc:
        raw_response_text = exc.raw_text
        await crud.add_generation_log(
            session=session,
            user_id=user_id,
            menu_date=menu_date,
            scope=scope.value,
            model=settings.gemini_menu_model,
            prompt_version=PROMPT_VERSION,
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
            target_slot=slot if scope == GenerationScope.SLOT else None,
        )
    except (ValidationError, ValueError) as exc:
        logger.warning("Generation response schema validation failed for user %s: %s", user_id, exc)
        await crud.add_generation_log(
            session=session,
            user_id=user_id,
            menu_date=menu_date,
            scope=scope.value,
            model=settings.gemini_menu_model,
            prompt_version=PROMPT_VERSION,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            raw_response=raw_response_text,
        )
        await session.commit()
        raise ServiceGenerationInvalidResponse(
            f"Generation response validation failed: {exc}", raw_response=raw_response_text
        ) from exc

    # 5. Group generated items by meal slot and apply in a single transaction via CRUD
    items_by_slot: dict[str, list[MenuItemCreate]] = defaultdict(list)
    for item_data in parsed_response.items:
        if scope == GenerationScope.SLOT and slot and item_data.slot != slot:
            continue
        items_by_slot[item_data.slot].append(
            MenuItemCreate(
                dish_id=item_data.dish_id,
                product_id=item_data.product_id,
                title=item_data.title,
                portion_g=item_data.portion_g,
                portion_text=item_data.portion_text,
                kcal_est=item_data.kcal_est,
                position=item_data.position,
            )
        )

    # Existing meals for today
    existing_menus_by_slot = {m.slot: m for m in context.existing_day_menus}

    for target_meal_slot, new_items in items_by_slot.items():
        existing_menu_read = existing_menus_by_slot.get(target_meal_slot)
        # If meal is already eaten or skipped, never overwrite it
        if existing_menu_read and existing_menu_read.status in (
            MenuStatus.EATEN,
            MenuStatus.SKIPPED,
        ):
            continue

        menu_entity = await crud.get_or_create_menu(
            session=session,
            user_id=user_id,
            menu_date=menu_date,
            slot=target_meal_slot,
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
        model=settings.gemini_menu_model,
        prompt_version=PROMPT_VERSION,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        raw_response=raw_response_text,
    )

    # Check completeness of all active meals in session
    await crud.evaluate_active_menus_completeness(session, user_id)

    # Commit all changes atomically
    await session.commit()

    # Re-read day's menus
    day_menus = await crud.get_day_menus(session, user_id, menu_date)

    return GenerationResultRead(
        menus=day_menus,
        rationale=parsed_response.rationale,
        shopping_list=parsed_response.shopping_list,
    )
