from fastapi import APIRouter, HTTPException, status

from auth.deps import CurrentUserId
from db import SessionDep
from nutrition import crud
from nutrition.models import MealType, PinnedMealRead, PinnedMealUpsert

router = APIRouter(prefix="/pinned-meals")


@router.get("", response_model=list[PinnedMealRead])
async def list_pinned_meals(
    session: SessionDep,
    user_id: CurrentUserId,
) -> list[PinnedMealRead]:
    return await crud.get_pinned_meals(session, user_id)


@router.put("/{meal_type}", response_model=PinnedMealRead)
async def upsert_pinned_meal(
    meal_type: MealType,
    data: PinnedMealUpsert,
    session: SessionDep,
    user_id: CurrentUserId,
) -> PinnedMealRead:
    profile = await crud.get_nutrition_profile(session, user_id)
    if (
        not profile
        or not profile.meals
        or not any(m.type == meal_type for m in profile.meals)
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Meal type '{meal_type.value}' is not in the profile schedule",
        )
    try:
        return await crud.upsert_pinned_meal(session, user_id, meal_type, data)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.delete("/{meal_type}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_pinned_meal(
    meal_type: MealType,
    session: SessionDep,
    user_id: CurrentUserId,
) -> None:
    deleted = await crud.delete_pinned_meal(session, user_id, meal_type)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pinned meal for meal type '{meal_type.value}' not found",
        )
