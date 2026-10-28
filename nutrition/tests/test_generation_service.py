from datetime import UTC, date, datetime
from uuid import uuid4

import pytest

from nutrition.models import (
    DishComponentRead,
    DishKind,
    DishRead,
    MealSchedule,
    MealType,
    MenuItemOrigin,
    MenuItemRead,
    MenuRead,
    MenuStatus,
    NutritionProfileRead,
    NutritionSource,
    ProductAvailability,
    ProductPreference,
    ProductRead,
    Sex,
)
from nutrition.service.context import GenerationContext
from nutrition.service.prompt import PROMPT_VERSION, build_generation_prompt
from nutrition.service.schema import (
    GEMINI_RESPONSE_SCHEMA,
    GeneratedMenuItem,
    GenerationResponse,
    build_gemini_response_schema,
    validate_generation_response,
)


def test_schema_item_validation_dish():
    dish_id = uuid4()

    # Valid: dish_id and portion_g (portion_text auto-filled from portion_g)
    item1 = GeneratedMenuItem(
        meal_type=MealType.BREAKFAST,
        dish_id=dish_id,
        title="Вівсянка",
        portion_g=200,
    )
    assert item1.dish_id == dish_id
    assert item1.portion_g == 200
    assert item1.portion_text == "200 г"

    # Invalid: missing portion_g
    with pytest.raises(ValueError):
        GeneratedMenuItem(
            meal_type=MealType.BREAKFAST,
            dish_id=dish_id,
            title="Вівсянка",
        )

    # Invalid: missing dish_id
    with pytest.raises(ValueError):
        GeneratedMenuItem(
            meal_type=MealType.BREAKFAST,
            title="Ніщо",
            portion_g=100,
        )


def test_validate_generation_response_limits():
    user_id = uuid4()
    dish_id = uuid4()
    const_dish_id = uuid4()
    prod_id = uuid4()
    now = datetime.now(UTC)

    ready_dish = DishRead(
        id=dish_id,
        user_id=user_id,
        name="Тушковане м'ясо",
        kind=DishKind.READY,
        cooked_weight_g=500,
        remaining_g=200,
        created_at=now,
        updated_at=now,
    )
    product = ProductRead(
        id=prod_id,
        user_id=user_id,
        name="Вівсяні пластівці",
        availability=ProductAvailability.IN_STOCK,
        preference=ProductPreference.PREFERRED,
        stock_g=100,
        reserved_g=20,
        available_g=80,
        created_at=now,
        updated_at=now,
    )
    const_dish = DishRead(
        id=const_dish_id,
        user_id=user_id,
        name="Вівсянка",
        kind=DishKind.CONSTANT,
        components=[
            DishComponentRead(
                id=uuid4(),
                dish_id=const_dish_id,
                product_id=prod_id,
                weight_g=50,
            )
        ],
        created_at=now,
        updated_at=now,
    )

    dishes_map = {dish_id: ready_dish, const_dish_id: const_dish}
    products_map = {prod_id: product}

    # 1. Successful validation
    valid_resp = GenerationResponse(
        rationale="Збалансований день",
        shopping_list=[],
        items=[
            GeneratedMenuItem(
                meal_type=MealType.LUNCH,
                dish_id=dish_id,
                title="Тушковане м'ясо",
                portion_g=150,
            ),
            GeneratedMenuItem(
                meal_type=MealType.BREAKFAST,
                dish_id=const_dish_id,
                title="Вівсянка",
                portion_g=50,
            ),
        ],
    )
    validate_generation_response(
        response=valid_resp,
        dishes_map=dishes_map,
        products_map=products_map,
        existing_menus=[],
    )

    # 2. Exceeds cumulative dish remaining
    dish_exceed_resp = GenerationResponse(
        rationale="Забагато м'яса",
        items=[
            GeneratedMenuItem(
                meal_type=MealType.LUNCH,
                dish_id=dish_id,
                title="Тушковане м'ясо",
                portion_g=120,
            ),
            GeneratedMenuItem(
                meal_type=MealType.DINNER,
                dish_id=dish_id,
                title="Тушковане м'ясо",
                portion_g=120,
            ),
        ],
    )
    match_msg = (
        "Cumulative portion 240g for ready dish 'Тушковане м'ясо' "
        "exceeds available remaining 200g"
    )
    with pytest.raises(ValueError, match=match_msg):
        validate_generation_response(
            response=dish_exceed_resp,
            dishes_map=dishes_map,
            products_map=products_map,
            existing_menus=[],
        )

    # 3. Exceeds product available stock for constant dish
    # Constant dish has 50g oats. portion_g=100g -> ratio 2.0 -> needs 100g oats > available 80g
    prod_exceed_resp = GenerationResponse(
        rationale="Забагато вівсянки",
        items=[
            GeneratedMenuItem(
                meal_type=MealType.BREAKFAST,
                dish_id=const_dish_id,
                title="Вівсянка",
                portion_g=100,
            )
        ],
    )
    with pytest.raises(ValueError, match="exceeds available stock for product 'Вівсяні пластівці'"):
        validate_generation_response(
            response=prod_exceed_resp,
            dishes_map=dishes_map,
            products_map=products_map,
            existing_menus=[],
        )


def test_build_generation_prompt():
    user_id = uuid4()
    now = datetime.now(UTC)

    profile = NutritionProfileRead(
        user_id=user_id,
        sex=Sex.MALE,
        birth_year=1976,
        height_cm=200,
        weight_kg=118.0,
        target_weight_kg=110.0,
        pace_kg_per_week=0.5,
        restrictions="Видалений жовчний міхур",
        dislikes="Печінка",
        meals=[
            MealSchedule(type=MealType.BREAKFAST, time="08:00"),
            MealSchedule(type=MealType.LUNCH, time="13:00"),
            MealSchedule(type=MealType.DINNER, time="19:00"),
            MealSchedule(type=MealType.EVENING_SNACK, time="21:00"),
        ],
        updated_at=now,
    )

    context = GenerationContext(
        user_id=user_id,
        date=date(2026, 10, 23),
        scope="day",
        target_meal_type=None,
        profile=profile,
        ready_dishes=[],
        constant_dishes=[],
        products=[],
        existing_day_menus=[],
        history_menus=[],
    )

    assert PROMPT_VERSION == "2026-10-28.1"
    prompt = build_generation_prompt(context)
    assert "2026-10-23" in prompt
    assert "Видалений жовчний міхур" in prompt
    assert "0,5–1 кг на тиждень" in prompt
    assert "вечірній перекус" in prompt


def test_build_generation_prompt_with_free_text_item():
    user_id = uuid4()
    now = datetime.now(UTC)
    menu_id = uuid4()

    profile = NutritionProfileRead(
        user_id=user_id,
        sex=Sex.MALE,
        birth_year=1980,
        height_cm=180,
        weight_kg=80.0,
        target_weight_kg=75.0,
        pace_kg_per_week=0.5,
        meals=[
            MealSchedule(type=MealType.BREAKFAST, time="08:00"),
        ],
        updated_at=now,
    )

    free_text_item = MenuItemRead(
        id=uuid4(),
        menu_id=menu_id,
        dish_id=None,
        title="Кава американо",
        portion_g=200,
        portion_text="200 мл",
        kcal=5.0,
        nutrition_source=NutritionSource.USER,
        origin=MenuItemOrigin.MANUAL,
        position=1,
        incomplete=False,
        updated_at=now,
    )

    day_menu = MenuRead(
        id=menu_id,
        user_id=user_id,
        date=date(2026, 10, 27),
        meal_type=MealType.BREAKFAST,
        status=MenuStatus.PLANNED,
        regenerations=0,
        created_at=now,
        updated_at=now,
        items=[free_text_item],
    )

    context = GenerationContext(
        user_id=user_id,
        date=date(2026, 10, 27),
        scope="day",
        target_meal_type=None,
        profile=profile,
        ready_dishes=[],
        constant_dishes=[],
        products=[],
        existing_day_menus=[day_menu],
        history_menus=[],
    )

    prompt = build_generation_prompt(context)
    assert "Кава американо" in prompt
    assert "200" in prompt
    assert "5" in prompt


def test_build_gemini_response_schema_enums_and_immutability():
    dish_id_1, dish_id_2 = uuid4(), uuid4()

    schema = build_gemini_response_schema([dish_id_1, dish_id_2])
    item_props = schema["properties"]["items"]["items"]["properties"]

    assert item_props["dish_id"]["format"] == "enum"
    assert item_props["dish_id"]["enum"] == [str(dish_id_1), str(dish_id_2)]

    # Product id must NOT be in schema
    assert "product_id" not in item_props

    # GEMINI_RESPONSE_SCHEMA must not be mutated
    base_props = GEMINI_RESPONSE_SCHEMA["properties"]["items"]["items"]["properties"]
    assert "enum" not in base_props["dish_id"]
    assert "format" not in base_props["dish_id"]
    assert "product_id" not in base_props


def test_build_gemini_response_schema_empty_lists():
    schema_empty = build_gemini_response_schema([])
    empty_props = schema_empty["properties"]["items"]["items"]["properties"]
    assert "enum" not in empty_props["dish_id"]
    assert "format" not in empty_props["dish_id"]
    assert "product_id" not in empty_props


def test_validate_generation_response_eaten_dish_not_double_counted():
    user_id = uuid4()
    dish_id = uuid4()
    menu_id = uuid4()
    now = datetime.now(UTC)

    # Ready dish had 500g cooked, 250g already eaten at Lunch -> remaining_g in DB is 250g
    couscous = DishRead(
        id=dish_id,
        user_id=user_id,
        name="каша кускус",
        kind=DishKind.READY,
        cooked_weight_g=500,
        remaining_g=250,
        created_at=now,
        updated_at=now,
    )
    eaten_lunch = MenuRead(
        id=menu_id,
        user_id=user_id,
        date=date(2026, 10, 24),
        meal_type=MealType.LUNCH,
        status=MenuStatus.EATEN,
        eaten_at=now,
        regenerations=1,
        created_at=now,
        updated_at=now,
        items=[
            MenuItemRead(
                id=uuid4(),
                menu_id=menu_id,
                dish_id=dish_id,
                title="каша кускус",
                portion_g=250,
                portion_text="250 г",
                kcal=280.0,
                nutrition_source=NutritionSource.COMPUTED,
                origin=MenuItemOrigin.GENERATED,
                position=1,
                updated_at=now,
            )
        ],
    )

    # Even if Gemini echoes the eaten Lunch item and proposes 200g for Dinner,
    # validation succeeds because Lunch is already deducted from remaining_g and locked.
    resp = GenerationResponse(
        rationale="Залишок кускусу на вечерю",
        items=[
            GeneratedMenuItem(
                meal_type=MealType.LUNCH,
                dish_id=dish_id,
                title="каша кускус",
                portion_g=250,
            ),
            GeneratedMenuItem(
                meal_type=MealType.DINNER,
                dish_id=dish_id,
                title="каша кускус",
                portion_g=200,
            ),
        ],
    )
    assert resp.items[0].meal_type == MealType.LUNCH
    assert resp.items[1].meal_type == MealType.DINNER

    validate_generation_response(
        response=resp,
        dishes_map={dish_id: couscous},
        products_map={},
        existing_menus=[eaten_lunch],
    )
