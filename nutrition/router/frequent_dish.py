from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from auth.deps import CurrentUserId
from config import Settings, get_settings
from db import SessionDep
from nutrition import crud
from nutrition.models import (
    DishRead,
    FrequentDishCookRequest,
    FrequentDishCreate,
    FrequentDishRead,
    FrequentDishUpdate,
)

router = APIRouter(prefix="/frequent-dishes")


@router.post("", response_model=FrequentDishRead, status_code=status.HTTP_201_CREATED)
async def create_frequent_dish(
    data: FrequentDishCreate,
    session: SessionDep,
    user_id: CurrentUserId,
    settings: Annotated[Settings, Depends(get_settings)],
) -> FrequentDishRead:
    return await crud.create_frequent_dish(session, user_id, data, settings=settings)


@router.get("", response_model=list[FrequentDishRead])
async def list_frequent_dishes(
    session: SessionDep,
    user_id: CurrentUserId,
) -> list[FrequentDishRead]:
    return await crud.get_frequent_dishes(session, user_id)


@router.get("/{frequent_dish_id}", response_model=FrequentDishRead)
async def get_frequent_dish(
    frequent_dish_id: UUID,
    session: SessionDep,
    user_id: CurrentUserId,
) -> FrequentDishRead:
    dish = await crud.get_frequent_dish(session, frequent_dish_id, user_id)
    if dish is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Frequent dish not found",
        )
    return dish


@router.patch("/{frequent_dish_id}", response_model=FrequentDishRead)
async def update_frequent_dish(
    frequent_dish_id: UUID,
    data: FrequentDishUpdate,
    session: SessionDep,
    user_id: CurrentUserId,
    settings: Annotated[Settings, Depends(get_settings)],
) -> FrequentDishRead:
    dish = await crud.update_frequent_dish(
        session, frequent_dish_id, user_id, data, settings=settings
    )
    if dish is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Frequent dish not found",
        )
    return dish


@router.delete("/{frequent_dish_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_frequent_dish(
    frequent_dish_id: UUID,
    session: SessionDep,
    user_id: CurrentUserId,
) -> None:
    deleted = await crud.delete_frequent_dish(session, frequent_dish_id, user_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Frequent dish not found",
        )


@router.post(
    "/{frequent_dish_id}/cook",
    response_model=DishRead,
    status_code=status.HTTP_201_CREATED,
)
async def cook_frequent_dish(
    frequent_dish_id: UUID,
    data: FrequentDishCookRequest,
    session: SessionDep,
    user_id: CurrentUserId,
    settings: Annotated[Settings, Depends(get_settings)],
) -> DishRead:
    return await crud.cook_frequent_dish(
        session,
        frequent_dish_id=frequent_dish_id,
        user_id=user_id,
        cooked_on=data.cooked_on,
        settings=settings,
    )
