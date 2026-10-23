from datetime import UTC, datetime
from uuid import UUID

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from nutrition.models import NutritionProfile, NutritionProfileUpsert


async def get_nutrition_profile(
    session: AsyncSession,
    user_id: UUID,
) -> NutritionProfile | None:
    """Return the nutrition profile for the given user, or None if not found."""
    statement = select(NutritionProfile).where(NutritionProfile.user_id == user_id)
    result = await session.exec(statement)
    return result.first()


async def upsert_nutrition_profile(
    session: AsyncSession,
    user_id: UUID,
    data: NutritionProfileUpsert,
) -> NutritionProfile:
    """Create or completely replace the user's nutrition profile."""
    profile = await get_nutrition_profile(session, user_id)
    if profile is None:
        profile = NutritionProfile.model_validate(data, update={"user_id": user_id})
        session.add(profile)
    else:
        updates = data.model_dump()
        for field, value in updates.items():
            setattr(profile, field, value)
        profile.updated_at = datetime.now(UTC)
        session.add(profile)
    await session.commit()
    await session.refresh(profile)
    return profile
