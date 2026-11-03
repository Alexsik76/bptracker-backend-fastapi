from datetime import date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import BaseModel

from auth.deps import CurrentUserId
from config import Settings, get_settings
from db import SessionDep
from nutrition import crud
from nutrition.models import (
    MenuCreate,
    MenuEvaluationRead,
    MenuItemCreate,
    MenuItemRead,
    MenuItemUpdate,
    MenuRead,
    MenuState,
    MenuUpdate,
)
from nutrition.service.evaluator import calculate_daily_norms, evaluate_menu_day_background
from nutrition.service.generator import (
    ServiceGeminiUnavailable,
    ServiceGenerationInvalidResponse,
    generate_suggestion,
)

menus_router = APIRouter(prefix="/menus")
items_router = APIRouter(prefix="/menu-items")
suggestions_router = APIRouter(prefix="/suggestions")
journal_router = APIRouter(prefix="/journal")


class EatMealRequest(BaseModel):
    date: date
    at: datetime


class SuggestionRequest(BaseModel):
    text: str | None = None
    date: date
    at: datetime


class SuggestionResponse(BaseModel):
    intent: str
    menu: MenuRead


class JournalTotals(BaseModel):
    kcal: float
    protein_g: float


class JournalNorms(BaseModel):
    kcal: float | None = None
    protein_g: float | None = None


class JournalResponse(BaseModel):
    date: date
    meals: list[MenuRead]
    suggestion: MenuRead | None = None
    totals: JournalTotals
    norms: JournalNorms | None = None
    day_evaluations: list[MenuEvaluationRead] = []


@menus_router.get("", response_model=list[MenuRead])
async def list_meals(
    session: SessionDep,
    user_id: CurrentUserId,
    date: Annotated[date, Query(description="Client local date")],
) -> list[MenuRead]:
    return await crud.list_meals(session, user_id, target_date=date)


@menus_router.post("", response_model=MenuRead, status_code=status.HTTP_201_CREATED)
async def create_meal(
    data: MenuCreate,
    session: SessionDep,
    user_id: CurrentUserId,
    background_tasks: BackgroundTasks,
    settings: Annotated[Settings, Depends(get_settings)],
) -> MenuRead:
    menu = await crud.create_menu(session, user_id, data)
    if menu.state == MenuState.EATEN:
        background_tasks.add_task(
            evaluate_menu_day_background,
            user_id=user_id,
            menu_date=menu.date,
            settings=settings,
            menu_id=menu.id,
        )
    return menu


@menus_router.get("/{menu_id}", response_model=MenuRead)
async def get_meal(
    menu_id: UUID,
    session: SessionDep,
    user_id: CurrentUserId,
) -> MenuRead:
    meal = await crud.get_menu(session, menu_id, user_id)
    if meal is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Meal not found",
        )
    return meal


@menus_router.patch("/{menu_id}", response_model=MenuRead)
async def update_meal(
    menu_id: UUID,
    data: MenuUpdate,
    session: SessionDep,
    user_id: CurrentUserId,
) -> MenuRead:
    meal = await crud.update_menu(session, menu_id, user_id, data)
    if meal is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Meal not found",
        )
    return meal


@menus_router.delete("/{menu_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_meal(
    menu_id: UUID,
    session: SessionDep,
    user_id: CurrentUserId,
    background_tasks: BackgroundTasks,
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    menu = await crud.get_menu_entity(session, menu_id, user_id)
    if menu is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Meal not found",
        )
    menu_state = menu.state
    menu_date = menu.date
    deleted = await crud.delete_menu(session, menu_id, user_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Meal not found",
        )
    if menu_state == MenuState.EATEN:
        background_tasks.add_task(
            evaluate_menu_day_background,
            user_id=user_id,
            menu_date=menu_date,
            settings=settings,
            menu_id=None,
        )


@menus_router.post(
    "/{menu_id}/items",
    response_model=MenuItemRead,
    status_code=status.HTTP_201_CREATED,
)
async def add_item_to_meal(
    menu_id: UUID,
    data: MenuItemCreate,
    session: SessionDep,
    user_id: CurrentUserId,
    background_tasks: BackgroundTasks,
    settings: Annotated[Settings, Depends(get_settings)],
) -> MenuItemRead:
    menu = await crud.get_menu_entity(session, menu_id, user_id)
    if menu is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Meal not found",
        )
    item = await crud.add_menu_item(session, user_id, menu_id, data)
    if menu.state == MenuState.EATEN:
        background_tasks.add_task(
            evaluate_menu_day_background,
            user_id=user_id,
            menu_date=menu.date,
            settings=settings,
            menu_id=menu.id,
        )
    return item


@menus_router.post("/{menu_id}/eat", response_model=MenuRead)
async def eat_meal(
    menu_id: UUID,
    data: EatMealRequest,
    session: SessionDep,
    user_id: CurrentUserId,
    background_tasks: BackgroundTasks,
    settings: Annotated[Settings, Depends(get_settings)],
) -> MenuRead:
    menu = await crud.eat_menu(
        session=session,
        menu_id=menu_id,
        user_id=user_id,
        target_date=data.date,
        eaten_at=data.at,
    )
    if menu is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Meal not found",
        )
    background_tasks.add_task(
        evaluate_menu_day_background,
        user_id=user_id,
        menu_date=data.date,
        settings=settings,
        menu_id=menu.id,
    )
    return menu


@items_router.patch("/{item_id}", response_model=MenuItemRead)
async def update_menu_item(
    item_id: UUID,
    data: MenuItemUpdate,
    session: SessionDep,
    user_id: CurrentUserId,
    background_tasks: BackgroundTasks,
    settings: Annotated[Settings, Depends(get_settings)],
) -> MenuItemRead:
    row = await crud.get_menu_item_with_menu(session, item_id, user_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Menu item not found",
        )
    _item, menu = row
    updated = await crud.update_menu_item(session, item_id, user_id, data)
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Menu item not found",
        )
    if menu.state == MenuState.EATEN:
        background_tasks.add_task(
            evaluate_menu_day_background,
            user_id=user_id,
            menu_date=menu.date,
            settings=settings,
            menu_id=menu.id,
        )
    return updated


@items_router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_menu_item(
    item_id: UUID,
    session: SessionDep,
    user_id: CurrentUserId,
    background_tasks: BackgroundTasks,
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    row = await crud.get_menu_item_with_menu(session, item_id, user_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Menu item not found",
        )
    _item, menu = row
    menu_state = menu.state
    menu_date = menu.date
    menu_id = menu.id
    deleted = await crud.delete_menu_item(session, item_id, user_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Menu item not found",
        )
    if menu_state == MenuState.EATEN:
        background_tasks.add_task(
            evaluate_menu_day_background,
            user_id=user_id,
            menu_date=menu_date,
            settings=settings,
            menu_id=menu_id,
        )


@suggestions_router.post("", response_model=SuggestionResponse)
async def create_suggestion(
    data: SuggestionRequest,
    session: SessionDep,
    user_id: CurrentUserId,
    background_tasks: BackgroundTasks,
    settings: Annotated[Settings, Depends(get_settings)],
) -> SuggestionResponse:
    try:
        intent, menu = await generate_suggestion(
            session=session,
            user_id=user_id,
            target_date=data.date,
            at=data.at,
            text=data.text,
            settings=settings,
        )
    except ServiceGeminiUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Gemini unavailable: {exc}",
        ) from exc
    except ServiceGenerationInvalidResponse as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Invalid response from model: {exc}",
        ) from exc

    if intent == "log":
        background_tasks.add_task(
            evaluate_menu_day_background,
            user_id=user_id,
            menu_date=data.date,
            settings=settings,
            menu_id=menu.id,
        )

    return SuggestionResponse(intent=intent, menu=menu)


@journal_router.get("", response_model=JournalResponse)
async def get_journal(
    session: SessionDep,
    user_id: CurrentUserId,
    background_tasks: BackgroundTasks,
    settings: Annotated[Settings, Depends(get_settings)],
    date: Annotated[date, Query(description="Client local date")],
) -> JournalResponse:
    materialized = await crud.materialize_pinned_dishes(session, user_id, date)
    for m in materialized:
        background_tasks.add_task(
            evaluate_menu_day_background,
            user_id=user_id,
            menu_date=date,
            settings=settings,
            menu_id=m.id,
        )

    meals, suggestion, total_kcal, total_protein = await crud.get_journal_data(
        session, user_id, date
    )
    day_evaluations = await crud.get_day_evaluations(session, user_id, date)

    profile = await crud.get_nutrition_profile(session, user_id)
    if profile:
        kcal_norm, protein_norm = calculate_daily_norms(profile, date)
        norms = JournalNorms(kcal=kcal_norm, protein_g=protein_norm)
    else:
        norms = None

    return JournalResponse(
        date=date,
        meals=meals,
        suggestion=suggestion,
        totals=JournalTotals(kcal=total_kcal, protein_g=total_protein),
        norms=norms,
        day_evaluations=day_evaluations,
    )
