from datetime import date
from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from auth.deps import CurrentUserId
from db import SessionDep
from nutrition import crud
from nutrition.models import (
    MenuCreate,
    MenuItemCreate,
    MenuItemRead,
    MenuItemUpdate,
    MenuRead,
    MenuUpdate,
)

menus_router = APIRouter(prefix="/menus")
items_router = APIRouter(prefix="/menu-items")


@menus_router.get("", response_model=list[MenuRead])
async def list_meals(
    session: SessionDep,
    user_id: CurrentUserId,
    date: date | None = None,
) -> list[MenuRead]:
    return await crud.list_meals(session, user_id, target_date=date)


@menus_router.post("", response_model=MenuRead, status_code=status.HTTP_201_CREATED)
async def create_meal(
    data: MenuCreate,
    session: SessionDep,
    user_id: CurrentUserId,
) -> MenuRead:
    return await crud.create_menu(session, user_id, data)


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
) -> None:
    deleted = await crud.delete_menu(session, menu_id, user_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Meal not found",
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
) -> MenuItemRead:
    menu = await crud.get_menu_entity(session, menu_id, user_id)
    if menu is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Meal not found",
        )
    return await crud.add_menu_item(session, user_id, menu_id, data)


@items_router.patch("/{item_id}", response_model=MenuItemRead)
async def update_menu_item(
    item_id: UUID,
    data: MenuItemUpdate,
    session: SessionDep,
    user_id: CurrentUserId,
) -> MenuItemRead:
    updated = await crud.update_menu_item(session, item_id, user_id, data)
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
    deleted = await crud.delete_menu_item(session, item_id, user_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Menu item not found",
        )
