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
    GenerationLog,
    MealType,
    MenuCreate,
    MenuItem,
    MenuItemCreate,
    NutritionSource,
)
from nutrition.service.estimator import (
    DISH_ESTIMATE_PROMPT_VERSION,
    PRODUCT_ESTIMATE_PROMPT_VERSION,
    build_dish_estimate_prompt,
    build_product_estimate_prompt,
    estimate_dish_nutrition,
    estimate_items_nutrition,
    estimate_product_nutrition,
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
            date=today,
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


@pytest.mark.asyncio
async def test_dish_estimate_prompt_composition():
    # 1. With composition: contains ingredients, weights, total weight
    comp = [("Вівсяні пластівці", 50), ("Яблуко", 100)]
    prompt_with_comp = build_dish_estimate_prompt("Вівсянка з яблуком", composition=comp)
    assert "Вівсяні пластівці: 50 г" in prompt_with_comp
    assert "Яблуко: 100 г" in prompt_with_comp
    assert "150" in prompt_with_comp
    assert "Вівсянка з яблуком" in prompt_with_comp
    assert "100 г" in prompt_with_comp

    # 2. Without composition or empty: prompt unchanged
    prompt_no_comp = build_dish_estimate_prompt("Вівсянка")
    prompt_empty_comp = build_dish_estimate_prompt("Вівсянка", composition=[])
    assert prompt_no_comp == prompt_empty_comp
    assert "Склад страви" not in prompt_no_comp
    assert "Вівсяні пластівці" not in prompt_no_comp


def test_product_estimate_prompt_unchanged():
    prod_prompt = build_product_estimate_prompt("Куряче філе")
    dish_prompt = build_dish_estimate_prompt("Куряче філе")
    assert prod_prompt == dish_prompt
    assert "Склад страви" not in prod_prompt
    assert PRODUCT_ESTIMATE_PROMPT_VERSION == "2026-10-27.1"
    assert DISH_ESTIMATE_PROMPT_VERSION != "2026-10-27.1"


@pytest.mark.asyncio
async def test_estimate_dish_nutrition_with_composition_and_generation_log(
    session: AsyncSession, test_user_id: UUID
):
    settings = get_settings()
    comp = [("Вівсяні пластівці", 50), ("Яблуко", 100)]

    mock_res = GeminiResult(
        data={
            "kcal": 110.0,
            "protein_g": 3.0,
            "fat_g": 1.5,
            "carbs_g": 22.0,
            "fiber_g": 2.0,
            "sugar_g": 8.0,
            "sodium_mg": 2.0,
        },
        raw_text='{"kcal": 110.0}',
        model="gemini-2.0-flash",
        input_tokens=25,
        output_tokens=20,
    )

    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_gemini:
        mock_gemini.return_value = mock_res
        est = await estimate_dish_nutrition(
            session=session,
            user_id=test_user_id,
            dish_name="Вівсянка з яблуком",
            settings=settings,
            composition=comp,
        )
        assert est is not None
        assert est.kcal == 110.0

        # Composition must appear in the prompt sent to Gemini
        call_prompt = mock_gemini.call_args.kwargs["prompt"]
        assert "Вівсяні пластівці: 50 г" in call_prompt
        assert "Яблуко: 100 г" in call_prompt
        assert "150" in call_prompt

        # Prompt in generation_log must contain composition
        log_entry = (
            await session.exec(
                select(GenerationLog)
                .where(
                    GenerationLog.user_id == test_user_id,
                    GenerationLog.scope == "dish_estimate",
                )
                .order_by(GenerationLog.created_at.desc())
            )
        ).first()
        assert log_entry is not None
        assert log_entry.prompt is not None
        assert "Вівсяні пластівці: 50 г" in log_entry.prompt
        assert "Яблуко: 100 г" in log_entry.prompt
        assert log_entry.prompt_version == DISH_ESTIMATE_PROMPT_VERSION


@pytest.mark.asyncio
async def test_estimate_product_nutrition_keeps_by_name(
    session: AsyncSession, test_user_id: UUID
):
    settings = get_settings()
    mock_res = GeminiResult(
        data={
            "kcal": 52.0,
            "protein_g": 0.3,
            "fat_g": 0.2,
            "carbs_g": 14.0,
            "fiber_g": 2.4,
            "sugar_g": 10.0,
            "sodium_mg": 1.0,
        },
        raw_text='{"kcal": 52.0}',
        model="gemini-2.0-flash",
        input_tokens=15,
        output_tokens=15,
    )
    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_gemini:
        mock_gemini.return_value = mock_res
        est = await estimate_product_nutrition(
            session=session,
            user_id=test_user_id,
            product_name="Яблуко",
            settings=settings,
        )
        assert est is not None
        assert est.kcal == 52.0
        call_prompt = mock_gemini.call_args.kwargs["prompt"]
        assert call_prompt == build_product_estimate_prompt("Яблуко")
        assert "Склад страви" not in call_prompt
