from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from auth.deps import CurrentUserId
from config import Settings, get_settings
from db import SessionDep
from nutrition import crud
from nutrition.models import (
    DayMenuRead,
    GenerationRequest,
    GenerationResultRead,
    MealCreate,
    MealType,
    MenuItemCreate,
    MenuItemRead,
    MenuItemUpdate,
    MenuNoteCreate,
    MenuNoteRead,
    MenuRead,
    MenuUpdate,
    OffPlanEntryRead,
)
from nutrition.service.estimator import estimate_day_kcal
from nutrition.service.generator import (
    InvalidGenerationRequest,
    NothingToGenerateError,
    ProfileNotFoundError,
    ServiceGeminiUnavailable,
    ServiceGenerationInvalidResponse,
    generate_menu,
)
from nutrition.service.notes import InvalidNoteRequest, process_menu_note
from nutrition.service.pinned import materialize_pinned_meals

menus_router = APIRouter(prefix="/menus")
items_router = APIRouter(prefix="/menu-items")


@menus_router.get("/{menu_date}", response_model=DayMenuRead)
async def get_day_menus(
    menu_date: date,
    session: SessionDep,
    user_id: CurrentUserId,
) -> DayMenuRead:
    await materialize_pinned_meals(session, user_id, menu_date)
    menus = await crud.get_day_menus(session, user_id, menu_date)
    notes = await crud.get_menu_notes(session, user_id, menu_date)
    off_plan = await crud.get_off_plan_entries(session, user_id, menu_date)
    profile = await crud.get_nutrition_profile(session, user_id)
    has_profile_meals = bool(profile and profile.meals)
    has_rows = await crud.has_menu_rows(session, user_id, menu_date)
    if not menus and not has_profile_meals and not has_rows and not notes and not off_plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Menu not found",
        )
    available_pins = await crud.get_available_pins(session, user_id, menu_date)
    return DayMenuRead(
        date=menu_date,
        menus=menus,
        notes=[MenuNoteRead.model_validate(n) for n in notes],
        off_plan=[OffPlanEntryRead.model_validate(e) for e in off_plan],
        available_pins=available_pins,
    )


@menus_router.post(
    "/{menu_date}/meals",
    response_model=MenuRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_meal_slot(
    menu_date: date,
    data: MealCreate,
    session: SessionDep,
    user_id: CurrentUserId,
) -> MenuRead:
    try:
        return await crud.create_or_revive_meal_slot(
            session=session,
            user_id=user_id,
            menu_date=menu_date,
            meal_type=data.meal_type,
            time=data.time,
        )
    except crud.MealSlotConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc


@menus_router.delete(
    "/{menu_date}/meals/{meal_type}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_meal_slot(
    menu_date: date,
    meal_type: MealType,
    session: SessionDep,
    user_id: CurrentUserId,
) -> None:
    try:
        deleted = await crud.delete_meal_slot(session, user_id, menu_date, meal_type)
    except crud.EatenMealImmutableError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Meal slot '{meal_type.value}' not found",
        )


@menus_router.post(
    "/{menu_date}/meals/{meal_type}/pin",
    response_model=MenuRead,
)
async def restore_pinned_meal(
    menu_date: date,
    meal_type: MealType,
    session: SessionDep,
    user_id: CurrentUserId,
) -> MenuRead:
    try:
        return await crud.restore_pinned_meal(session, user_id, menu_date, meal_type)
    except crud.EatenMealImmutableError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc.args[0]),
        ) from exc


@menus_router.patch("/{menu_id}", response_model=MenuRead)
async def update_menu(
    menu_id: UUID,
    data: MenuUpdate,
    session: SessionDep,
    user_id: CurrentUserId,
) -> MenuRead:
    updated = await crud.update_menu(session, menu_id, user_id, data)
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Menu not found",
        )
    return updated


@menus_router.post("/{menu_date}/generate", response_model=GenerationResultRead)
async def generate_menu_items(
    menu_date: date,
    data: GenerationRequest,
    session: SessionDep,
    user_id: CurrentUserId,
    settings: Annotated[Settings, Depends(get_settings)],
) -> GenerationResultRead:
    try:
        return await generate_menu(
            session=session,
            user_id=user_id,
            menu_date=menu_date,
            scope=data.scope,
            meal_type=data.meal_type,
            settings=settings,
        )
    except (ProfileNotFoundError, NothingToGenerateError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except ServiceGeminiUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Gemini service is unavailable",
        ) from exc
    except ServiceGenerationInvalidResponse as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    except InvalidGenerationRequest as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@menus_router.post("/{menu_date}/estimate", response_model=DayMenuRead)
async def estimate_menu_items(
    menu_date: date,
    session: SessionDep,
    user_id: CurrentUserId,
    settings: Annotated[Settings, Depends(get_settings)],
    meal_type: MealType | None = None,
) -> DayMenuRead:
    try:
        return await estimate_day_kcal(
            session=session,
            user_id=user_id,
            menu_date=menu_date,
            settings=settings,
            meal_type=meal_type,
        )
    except (ProfileNotFoundError, NothingToGenerateError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except ServiceGeminiUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Gemini service is unavailable",
        ) from exc
    except ServiceGenerationInvalidResponse as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    except InvalidGenerationRequest as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@menus_router.post(
    "/{menu_date}/notes",
    response_model=MenuNoteRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_menu_note(
    menu_date: date,
    data: MenuNoteCreate,
    session: SessionDep,
    user_id: CurrentUserId,
    settings: Annotated[Settings, Depends(get_settings)],
) -> MenuNoteRead:
    try:
        return await process_menu_note(
            session=session,
            user_id=user_id,
            menu_date=menu_date,
            data=data,
            settings=settings,
        )
    except (ProfileNotFoundError, NothingToGenerateError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except ServiceGeminiUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Gemini service is unavailable",
        ) from exc
    except ServiceGenerationInvalidResponse as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    except (InvalidNoteRequest, InvalidGenerationRequest) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@menus_router.post(
    "/{menu_date}/items",
    response_model=MenuItemRead,
    status_code=status.HTTP_201_CREATED,
)
async def add_menu_item(
    menu_date: date,
    meal_type: MealType,
    data: MenuItemCreate,
    session: SessionDep,
    user_id: CurrentUserId,
) -> MenuItemRead:
    try:
        return await crud.add_menu_item(session, user_id, menu_date, meal_type, data)
    except (crud.MealSlotRemovedError, crud.EatenMealImmutableError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc.args[0]),
        ) from exc


@items_router.patch("/{item_id}", response_model=MenuItemRead)
async def update_menu_item(
    item_id: UUID,
    data: MenuItemUpdate,
    session: SessionDep,
    user_id: CurrentUserId,
) -> MenuItemRead:
    try:
        updated = await crud.update_menu_item(session, item_id, user_id, data)
    except crud.EatenMealImmutableError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc.args[0]),
        ) from exc
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Menu item not found",
        )
    return updated


@items_router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_menu_item(
    item_id: UUID,
    session: SessionDep,
    user_id: CurrentUserId,
) -> None:
    try:
        deleted = await crud.delete_menu_item(session, item_id, user_id)
    except crud.EatenMealImmutableError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Menu item not found",
        )
