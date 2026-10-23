from fastapi import APIRouter, HTTPException, status

from auth.deps import CurrentUserId
from db import SessionDep
from nutrition import crud
from nutrition.models import NutritionProfile, NutritionProfileRead, NutritionProfileUpsert

router = APIRouter(prefix="/profile")


@router.get("", response_model=NutritionProfileRead)
async def get_profile(
    session: SessionDep,
    user_id: CurrentUserId,
) -> NutritionProfile:
    profile = await crud.get_nutrition_profile(session, user_id)
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Nutrition profile not found",
        )
    return profile


@router.put("", response_model=NutritionProfileRead)
async def upsert_profile(
    data: NutritionProfileUpsert,
    session: SessionDep,
    user_id: CurrentUserId,
) -> NutritionProfile:
    return await crud.upsert_nutrition_profile(session, user_id, data)
