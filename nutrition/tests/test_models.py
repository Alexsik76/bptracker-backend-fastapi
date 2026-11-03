from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from nutrition.clock import utc_today
from nutrition.models import (
    Dish,
    DishComponent,
    GenerationLog,
    MealType,
    Menu,
    MenuDayEvaluation,
    MenuItem,
    MenuOrigin,
    MenuState,
    NutritionProfile,
    NutritionSource,
    Product,
    Sex,
)


@pytest.mark.asyncio
async def test_nutrition_profile_persistence(session: AsyncSession, test_user_id: UUID):
    profile = NutritionProfile(
        user_id=test_user_id,
        sex=Sex.MALE,
        birth_year=1976,
        height_cm=200,
        weight_kg=118.0,
        target_weight_kg=110.0,
        pace_kg_per_week=0.75,
        restrictions="Low salt",
        dislikes="Liver",
        weight_variety=0.6,
        weight_balance=0.7,
        weight_goal=0.8,
    )
    session.add(profile)
    await session.commit()
    await session.refresh(profile)

    stmt = select(NutritionProfile).where(NutritionProfile.user_id == test_user_id)
    saved = (await session.exec(stmt)).first()
    assert saved is not None
    assert saved.sex == Sex.MALE
    assert saved.birth_year == 1976
    assert saved.weight_kg == 118.0
    assert saved.weight_variety == 0.6
    assert saved.weight_balance == 0.7
    assert saved.weight_goal == 0.8
    assert saved.updated_at is not None


@pytest.mark.asyncio
async def test_product_persistence(session: AsyncSession, test_user_id: UUID):
    product = Product(
        user_id=test_user_id,
        name="Вівсянка",
        available=True,
        note="органічна",
    )
    session.add(product)
    await session.commit()
    await session.refresh(product)

    assert product.id is not None
    assert product.created_at is not None
    assert product.updated_at is not None

    stmt = select(Product).where(Product.user_id == test_user_id)
    saved = (await session.exec(stmt)).first()
    assert saved is not None
    assert saved.name == "Вівсянка"
    assert saved.available is True
    assert saved.note == "органічна"


@pytest.mark.asyncio
async def test_dish_and_components_persistence(session: AsyncSession, test_user_id: UUID):
    p1 = Product(user_id=test_user_id, name="Вівсяні пластівці", available=True)
    p2 = Product(user_id=test_user_id, name="Яблуко", available=True)
    session.add_all([p1, p2])
    await session.commit()
    await session.refresh(p1)
    await session.refresh(p2)

    dish = Dish(
        user_id=test_user_id,
        name="Вівсянка з яблуком",
        is_ready=True,
        pinned_meal_type=MealType.BREAKFAST,
        nutrition_overridden=True,
        note="Постійна ранкова страва",
        kcal=150.0,
        protein_g=5.0,
        fat_g=2.0,
        carbs_g=25.0,
    )
    session.add(dish)
    await session.commit()
    await session.refresh(dish)

    assert dish.id is not None
    assert dish.is_ready is True
    assert dish.pinned_meal_type == MealType.BREAKFAST

    comp1 = DishComponent(dish_id=dish.id, product_id=p1.id, weight_g=45)
    comp2 = DishComponent(dish_id=dish.id, product_id=p2.id, weight_g=100)
    session.add_all([comp1, comp2])
    await session.commit()

    stmt = select(DishComponent).where(DishComponent.dish_id == dish.id)
    comps = (await session.exec(stmt)).all()
    assert len(comps) == 2
    comp_map = {c.product_id: c.weight_g for c in comps}
    assert comp_map == {p1.id: 45, p2.id: 100}


@pytest.mark.asyncio
async def test_menu_and_item_persistence(session: AsyncSession, test_user_id: UUID):
    now_dt = datetime.now(UTC)
    menu = Menu(
        user_id=test_user_id,
        meal_type=MealType.LUNCH,
        origin=MenuOrigin.MANUAL,
        state=MenuState.EATEN,
        eaten_at=now_dt,
    )
    session.add(menu)
    await session.commit()
    await session.refresh(menu)

    assert menu.id is not None
    assert menu.origin == MenuOrigin.MANUAL
    assert menu.state == MenuState.EATEN

    item = MenuItem(
        menu_id=menu.id,
        title="Борщ",
        portion_g=300,
        portion_text="300 г",
        kcal=180.0,
        protein_g=10.0,
        nutrition_source=NutritionSource.USER,
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)

    assert item.id is not None
    assert item.menu_id == menu.id


@pytest.mark.asyncio
async def test_generation_log_with_menu_id(session: AsyncSession, test_user_id: UUID):
    menu = Menu(
        user_id=test_user_id,
        meal_type=MealType.DINNER,
        origin=MenuOrigin.SUGGESTION,
        state=MenuState.SUGGESTION,
    )
    session.add(menu)
    await session.commit()
    await session.refresh(menu)

    log = GenerationLog(
        user_id=test_user_id,
        menu_id=menu.id,
        date=utc_today(),
        scope="single_meal",
        model="gemini-2.0-flash",
        prompt_version="2026-11-02.1",
        prompt="prompt",
        duration_ms=120,
    )
    session.add(log)
    await session.commit()
    await session.refresh(log)

    assert log.id is not None
    assert log.menu_id == menu.id


@pytest.mark.asyncio
async def test_menu_day_evaluation_with_menu_id(session: AsyncSession, test_user_id: UUID):
    menu = Menu(
        user_id=test_user_id,
        meal_type=MealType.BREAKFAST,
        origin=MenuOrigin.MANUAL,
        state=MenuState.EATEN,
        eaten_at=datetime.now(UTC),
    )
    session.add(menu)
    await session.commit()
    await session.refresh(menu)

    ev = MenuDayEvaluation(
        user_id=test_user_id,
        date=utc_today(),
        criterion_key="balance",
        criteria_version=1,
        choice="good",
        confidence=0.9,
        probabilities={"good": 0.9, "bad": 0.1},
        model="jev",
        menu_id=menu.id,
    )
    session.add(ev)
    await session.commit()
    await session.refresh(ev)

    assert ev.id is not None
    assert ev.menu_id == menu.id
