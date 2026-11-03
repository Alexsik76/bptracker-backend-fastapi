from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from config import get_settings
from gemini_infra import GeminiResult, GeminiUnavailable
from nutrition import crud
from nutrition.clock import utc_today
from nutrition.models import (
    MealType,
    MenuCreate,
    MenuItem,
    MenuItemCreate,
    NutritionSource,
)
from nutrition.service.estimator import (
    estimate_dish_nutrition,
    estimate_items_nutrition,
)
from nutrition.service.generator import ServiceGeminiUnavailable


@pytest.mark.asyncio
async def test_estimate_dish_nutrition(session: AsyncSession, test_user_id: UUID):
    settings = get_settings()

    mock_res = GeminiResult(
        data={
            "kcal": 165.0,
            "protein_g": 31.0,
            "fat_g": 3.6,
            "carbs_g": 0.0,
            "fiber_g": None,
            "sugar_g": None,
            "sodium_mg": 74.0,
        },
        raw_text='{"kcal": 165.0}',
        model="gemini-2.0-flash",
        input_tokens=15,
        output_tokens=15,
    )
    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_gemini:
        mock_gemini.return_value = mock_res
        est = await estimate_dish_nutrition(
            session=session,
            user_id=test_user_id,
            dish_name="Куряче філе",
            settings=settings,
        )
        assert est is not None
        assert est.kcal == 165.0
        assert est.protein_g == 31.0
        assert est.fat_g == 3.6
        assert est.carbs_g == 0.0
        assert est.sodium_mg == 74.0


@pytest.mark.asyncio
async def test_estimate_dish_nutrition_error(session: AsyncSession, test_user_id: UUID):
    settings = get_settings()

    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_gemini:
        mock_gemini.side_effect = GeminiUnavailable("Timeout")
        # With raise_on_error=False, returns None
        est = await estimate_dish_nutrition(
            session=session,
            user_id=test_user_id,
            dish_name="Куряче філе",
            settings=settings,
            raise_on_error=False,
        )
        assert est is None

        # With raise_on_error=True, raises ServiceGeminiUnavailable
        with pytest.raises(ServiceGeminiUnavailable):
            await estimate_dish_nutrition(
                session=session,
                user_id=test_user_id,
                dish_name="Куряче філе",
                settings=settings,
                raise_on_error=True,
            )


@pytest.mark.asyncio
async def test_estimate_items_nutrition(session: AsyncSession, test_user_id: UUID):
    settings = get_settings()
    today = utc_today()

    meal = await crud.create_menu(
        session,
        test_user_id,
        MenuCreate(
            meal_type=MealType.LUNCH,
            items=[
                MenuItemCreate(title="Борщ український", portion_g=300),
                MenuItemCreate(title="Пампушка з часником", portion_g=50),
            ],
        ),
    )

    items = list(meal.items)
    item1_id = items[0].id
    item2_id = items[1].id

    mock_res = GeminiResult(
        data={
            "items": [
                {
                    "id": str(item1_id),
                    "kcal": 180.0,
                    "protein_g": 8.0,
                    "fat_g": 6.0,
                    "carbs_g": 20.0,
                },
                {
                    "id": str(item2_id),
                    "kcal": 150.0,
                    "protein_g": 4.0,
                    "fat_g": 5.0,
                    "carbs_g": 22.0,
                },
            ]
        },
        raw_text="{}",
        model="gemini-2.0-flash",
        input_tokens=30,
        output_tokens=30,
    )

    db_items = list((await session.exec(select(MenuItem).where(MenuItem.menu_id == meal.id))).all())

    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_gemini:
        mock_gemini.return_value = mock_res
        estimated = await estimate_items_nutrition(
            session=session,
            user_id=test_user_id,
            items=db_items,
            settings=settings,
            menu_date=today,
        )
        assert len(estimated) == 2
        by_id = {i.id: i for i in estimated}
        assert by_id[item1_id].kcal == 180.0
        assert by_id[item1_id].nutrition_source == NutritionSource.MODEL
        assert by_id[item2_id].kcal == 150.0
