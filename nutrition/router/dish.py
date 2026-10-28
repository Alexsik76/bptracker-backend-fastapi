from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from auth.deps import CurrentUserId
from config import Settings, get_settings
from db import SessionDep
from nutrition import crud
from nutrition.models import DishCreate, DishRead, DishUpdate

router = APIRouter(prefix="/dishes")


@router.post("", response_model=DishRead, status_code=status.HTTP_201_CREATED)
async def create_dish(
    data: DishCreate,
    session: SessionDep,
    user_id: CurrentUserId,
    settings: Annotated[Settings, Depends(get_settings)],
) -> DishRead:
    return await crud.create_dish(session, user_id, data, settings=settings)


@router.get("", response_model=list[DishRead])
async def list_dishes(
    session: SessionDep,
    user_id: CurrentUserId,
    available: bool | None = None,
) -> list[DishRead]:
    return await crud.get_dishes(session, user_id, available=available)


@router.get("/{dish_id}", response_model=DishRead)
async def get_dish(
    dish_id: UUID,
    session: SessionDep,
    user_id: CurrentUserId,
) -> DishRead:
    dish = await crud.get_dish(session, dish_id, user_id)
    if dish is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dish not found",
        )
    return dish


@router.patch("/{dish_id}", response_model=DishRead)
async def update_dish(
    dish_id: UUID,
    data: DishUpdate,
    session: SessionDep,
    user_id: CurrentUserId,
    settings: Annotated[Settings, Depends(get_settings)],
) -> DishRead:
    dish = await crud.update_dish(session, dish_id, user_id, data, settings=settings)
    if dish is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dish not found",
        )
    return dish


@router.delete("/{dish_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_dish(
    dish_id: UUID,
    session: SessionDep,
    user_id: CurrentUserId,
) -> None:
    deleted = await crud.delete_dish(session, dish_id, user_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dish not found",
        )
