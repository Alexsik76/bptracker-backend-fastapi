from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from nutrition.models import (
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
    dish2_id = uuid4()
    recipe_id = uuid4()
    prod_id = uuid4()
    now = datetime.now(UTC)

    ready_dish = DishRead(
        id=dish_id,
        user_id=user_id,
        recipe_id=recipe_id,
        name="Тушковане м'ясо",
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
        created_at=now,
        updated_at=now,
    )
    dish2 = DishRead(
        id=dish2_id,
        user_id=user_id,
        recipe_id=recipe_id,
        name="Вівсянка",
        cooked_weight_g=300,
        remaining_g=150,
        created_at=now,
        updated_at=now,
    )

    dishes_map = {dish_id: ready_dish, dish2_id: dish2}
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
                dish_id=dish2_id,
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
        "Cumulative portion 240g for dish 'Тушковане м'ясо' "
        "exceeds available remaining 200g"
    )
    with pytest.raises(ValueError, match=match_msg):
        validate_generation_response(
            response=dish_exceed_resp,
            dishes_map=dishes_map,
            products_map=products_map,
            existing_menus=[],
        )


def test_build_generation_prompt():
    test_day = datetime.now(UTC).date() + timedelta(days=2)
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
        date=test_day,
        scope="day",
        target_meal_type=None,
        profile=profile,
        dishes=[],
        products=[],
        existing_day_menus=[],
        history_menus=[],
    )

    assert PROMPT_VERSION == "2026-10-28.2"
    prompt = build_generation_prompt(context)
    assert str(test_day) in prompt
    assert "Видалений жовчний міхур" in prompt
    assert "0,5–1 кг на тиждень" in prompt
    assert "вечірній перекус" in prompt


def test_build_generation_prompt_with_free_text_item():
    test_day = datetime.now(UTC).date() + timedelta(days=2)
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
        date=test_day,
        meal_type=MealType.BREAKFAST,
        status=MenuStatus.PLANNED,
        regenerations=0,
        created_at=now,
        updated_at=now,
        items=[free_text_item],
    )

    context = GenerationContext(
        user_id=user_id,
        date=test_day,
        scope="day",
        target_meal_type=None,
        profile=profile,
        dishes=[],
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
    test_day = datetime.now(UTC).date() + timedelta(days=2)
    user_id = uuid4()
    dish_id = uuid4()
    menu_id = uuid4()
    now = datetime.now(UTC)

    # Dish had 500g cooked, 250g already eaten at Lunch -> remaining_g in DB is 250g
    couscous = DishRead(
        id=dish_id,
        user_id=user_id,
        recipe_id=uuid4(),
        name="каша кускус",
        cooked_weight_g=500,
        remaining_g=250,
        created_at=now,
        updated_at=now,
    )
    eaten_lunch = MenuRead(
        id=menu_id,
        user_id=user_id,
        date=test_day,
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


def test_validate_generation_response_planned_other_meal_not_double_counted():
    test_day = datetime.now(UTC).date() + timedelta(days=2)
    user_id = uuid4()
    dish_id = uuid4()
    menu_id = uuid4()
    now = datetime.now(UTC)

    # 500g cooked, nothing eaten; planned uneaten Lunch reserves 250g, so the
    # generation context passes an effective remaining_g of 250g.
    buckwheat = DishRead(
        id=dish_id,
        user_id=user_id,
        recipe_id=uuid4(),
        name="гречка варена",
        cooked_weight_g=500,
        remaining_g=250,
        created_at=now,
        updated_at=now,
    )
    planned_lunch = MenuRead(
        id=menu_id,
        user_id=user_id,
        date=test_day,
        meal_type=MealType.LUNCH,
        status=MenuStatus.PLANNED,
        eaten_at=None,
        regenerations=0,
        created_at=now,
        updated_at=now,
        items=[
            MenuItemRead(
                id=uuid4(),
                menu_id=menu_id,
                dish_id=dish_id,
                title="гречка варена",
                portion_g=250,
                portion_text="250 г",
                kcal=257.0,
                nutrition_source=NutritionSource.COMPUTED,
                origin=MenuItemOrigin.GENERATED,
                position=1,
                updated_at=now,
            )
        ],
    )

    def dinner(portion: int) -> GenerationResponse:
        return GenerationResponse(
            rationale="Гречка на вечерю",
            items=[
                GeneratedMenuItem(
                    meal_type=MealType.DINNER,
                    dish_id=dish_id,
                    title="гречка варена",
                    portion_g=portion,
                )
            ],
        )

    validate_generation_response(
        response=dinner(250),
        dishes_map={dish_id: buckwheat},
        products_map={},
        existing_menus=[planned_lunch],
        target_meal_type=MealType.DINNER,
    )

    with pytest.raises(ValueError, match="exceeds available remaining 250g"):
        validate_generation_response(
            response=dinner(300),
            dishes_map={dish_id: buckwheat},
            products_map={},
            existing_menus=[planned_lunch],
            target_meal_type=MealType.DINNER,
        )


def test_generator_context_without_recipes():
    test_day = datetime.now(UTC).date() + timedelta(days=2)
    user_id = uuid4()
    now = datetime.now(UTC)

    # GenerationContext should have dishes and products, but NO recipes
    context = GenerationContext(
        user_id=user_id,
        date=test_day,
        scope="day",
        target_meal_type=None,
        profile=None,
        dishes=[
            DishRead(
                id=uuid4(),
                user_id=user_id,
                recipe_id=uuid4(),
                name="Гречана каша",
                cooked_weight_g=400,
                remaining_g=200,
                created_at=now,
                updated_at=now,
            )
        ],
        products=[
            ProductRead(
                id=uuid4(),
                user_id=user_id,
                name="Яблуко",
                availability=ProductAvailability.IN_STOCK,
                preference=ProductPreference.NEUTRAL,
                stock_g=500,
                created_at=now,
                updated_at=now,
            )
        ],
        existing_day_menus=[],
        history_menus=[],
    )

    # GenerationContext has no recipes attribute
    assert not hasattr(context, "recipes")
    assert not hasattr(context, "constant_dishes")

    prompt = build_generation_prompt(context)
    # Prompt contains dishes section
    assert "Гречана каша" in prompt
    assert "200" in prompt  # remaining_g
    # Products are passed for shopping list
    assert "Яблуко" in prompt
    # No recipes section or constant dishes or eat_raw
    assert "constant_dish" not in prompt.lower()
    assert "постійні страви" not in prompt.lower()
    assert "рецепт" not in prompt.lower()
    assert "eat_raw" not in prompt.lower()

