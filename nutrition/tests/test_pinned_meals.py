from datetime import date as dt_date
from datetime import timedelta
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from gemini_infra.types import GeminiResult
from nutrition.models import (
    MealType,
    Menu,
    MenuRead,
    MenuStatus,
    compute_day_generation_flags,
    is_meal_generatable,
)


async def init_profile(client: AsyncClient):
    resp = await client.put(
        "/nutrition/profile",
        json={
            "sex": "male",
            "birth_year": 1985,
            "height_cm": 180,
            "weight_kg": 80.0,
            "target_weight_kg": 75.0,
            "pace_kg_per_week": 0.5,
            "meals": [
                {"type": "breakfast", "time": "08:00"},
                {"type": "lunch", "time": "13:00"},
                {"type": "dinner", "time": "19:00"},
            ],
        },
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_pin_crud_and_slot_validation(client: AsyncClient):
    await init_profile(client)
    # 1. 400 when meal type is not in profile schedule
    resp = await client.put(
        "/nutrition/pinned-meals/afternoon_snack",
        json={
            "active": True,
            "items": [{"title": "Яблуко", "portion_g": 150, "kcal": 80.0}],
        },
    )
    assert resp.status_code == 400
    assert "not in the profile schedule" in resp.json()["detail"]

    # 2. Reference validation on items:
    # A) Empty title when dish_id is not provided -> 422
    resp = await client.put(
        "/nutrition/pinned-meals/breakfast",
        json={
            "active": True,
            "items": [{"portion_g": 100}],
        },
    )
    assert resp.status_code == 422

    # 3. Create a dish to test title auto-population
    dish_resp = await client.post(
        "/nutrition/dishes",
        json={
            "name": "Вівсянка з медом",
            "kind": "ready",
            "cooked_weight_g": 400,
            "cooked_on": str(dt_date.today()),
        },
    )
    assert dish_resp.status_code == 201
    dish_id = dish_resp.json()["id"]

    # 4. Successfully PUT pinned meal with dish and free text
    put_resp = await client.put(
        "/nutrition/pinned-meals/breakfast",
        json={
            "active": True,
            "items": [
                {
                    "dish_id": dish_id,
                    "portion_g": 200,
                    "portion_text": "200 г",
                    "kcal": 250.0,
                    "position": 1,
                },
                {
                    "title": "Горіхи волоські",
                    "portion_g": 30,
                    "portion_text": "30 г",
                    "kcal": 195.0,
                    "position": 2,
                },
                {
                    "title": "Кава чорна",
                    "portion_text": "1 горнятко",
                    "kcal": 5.0,
                    "position": 3,
                },
            ],
        },
    )
    assert put_resp.status_code == 200
    pin_data = put_resp.json()
    assert pin_data["meal_type"] == "breakfast"
    assert pin_data["active"] is True
    assert len(pin_data["items"]) == 3
    assert pin_data["items"][0]["title"] == "Вівсянка з медом"
    assert pin_data["items"][0]["dish_id"] == dish_id
    assert pin_data["items"][1]["title"] == "Горіхи волоські"
    assert pin_data["items"][2]["title"] == "Кава чорна"

    # 5. GET /pinned-meals lists the pinned meal
    list_resp = await client.get("/nutrition/pinned-meals")
    assert list_resp.status_code == 200
    pins_list = list_resp.json()
    assert len(pins_list) == 1
    assert pins_list[0]["meal_type"] == "breakfast"

    # 6. DELETE /pinned-meals/{meal_type}
    del_resp = await client.delete("/nutrition/pinned-meals/breakfast")
    assert del_resp.status_code == 204

    # Now GET /pinned-meals is empty
    list_resp2 = await client.get("/nutrition/pinned-meals")
    assert list_resp2.status_code == 200
    assert len(list_resp2.json()) == 0

    # DELETE non-existent returns 404
    del_404 = await client.delete("/nutrition/pinned-meals/breakfast")
    assert del_404.status_code == 404


@pytest.mark.asyncio
async def test_materialization_on_read(client: AsyncClient, session: AsyncSession):
    await init_profile(client)
    today = dt_date.today()
    future = today + timedelta(days=2)
    past = today - timedelta(days=1)

    # Create active pin for breakfast
    await client.put(
        "/nutrition/pinned-meals/breakfast",
        json={
            "active": True,
            "items": [
                {"title": "Омлет", "portion_g": 180, "kcal": 270.0, "position": 1},
                {"title": "Чай", "portion_text": "250 мл", "position": 2},
            ],
        },
    )

    # 1. Past date: materialization does NOT run
    past_resp = await client.get(f"/nutrition/menus/{past}")
    assert past_resp.status_code == 200
    past_day = past_resp.json()
    past_breakfast = next(m for m in past_day["menus"] if m["meal_type"] == "breakfast")
    assert past_breakfast["id"] is None
    assert past_breakfast["pinned"] is False
    assert past_breakfast["items"] == []
    # Pin is available to be restored on past date if desired
    assert any(p["meal_type"] == "breakfast" for p in past_day["available_pins"])

    # Verify no Menu row exists in DB for past date
    stmt_past = select(Menu).where(Menu.date == past, Menu.meal_type == MealType.BREAKFAST)
    assert (await session.exec(stmt_past)).first() is None

    # 2. Today: materialization runs
    today_resp = await client.get(f"/nutrition/menus/{today}")
    assert today_resp.status_code == 200
    today_day = today_resp.json()
    today_breakfast = next(m for m in today_day["menus"] if m["meal_type"] == "breakfast")
    assert today_breakfast["id"] is not None
    assert today_breakfast["pinned"] is True
    assert today_breakfast["time"] == "08:00"
    assert len(today_breakfast["items"]) == 2
    assert today_breakfast["items"][0]["title"] == "Омлет"
    assert today_breakfast["items"][0]["origin"] == "manual"
    assert today_breakfast["items"][0]["nutrition_source"] == "user"
    assert today_breakfast["items"][0]["kcal"] == 270.0
    assert today_breakfast["items"][1]["title"] == "Чай"
    assert today_breakfast["items"][1]["nutrition_source"] is None
    assert today_breakfast["items"][1]["kcal"] is None
    # Because it is currently pinned, it is NOT in available_pins
    assert not any(p["meal_type"] == "breakfast" for p in today_day["available_pins"])

    # 3. Read today again: materialization does NOT duplicate rows or items
    today_resp2 = await client.get(f"/nutrition/menus/{today}")
    today_day2 = today_resp2.json()
    today_breakfast2 = next(m for m in today_day2["menus"] if m["meal_type"] == "breakfast")
    assert len(today_breakfast2["items"]) == 2

    # 4. Future date: materializes as well
    fut_resp = await client.get(f"/nutrition/menus/{future}")
    assert fut_resp.status_code == 200
    fut_day = fut_resp.json()
    fut_breakfast = next(m for m in fut_day["menus"] if m["meal_type"] == "breakfast")
    assert fut_breakfast["id"] is not None
    assert fut_breakfast["pinned"] is True
    assert len(fut_breakfast["items"]) == 2


@pytest.mark.asyncio
async def test_pin_update_resyncs_untouched_future_rows(client: AsyncClient):
    await init_profile(client)
    today = dt_date.today()
    future = today + timedelta(days=2)

    # Create active pin for breakfast
    await client.put(
        "/nutrition/pinned-meals/breakfast",
        json={
            "active": True,
            "items": [{"title": "Сирники", "portion_g": 150, "kcal": 320.0}],
        },
    )

    # Materialize on today and future
    today_res = await client.get(f"/nutrition/menus/{today}")
    fut_res = await client.get(f"/nutrition/menus/{future}")
    assert today_res.status_code == 200
    assert fut_res.status_code == 200

    today_menu_id = today_res.json()["menus"][0]["id"]

    # Mark meal on today as eaten
    mark_res = await client.patch(
        f"/nutrition/menus/{today_menu_id}",
        json={"eaten_at": "2026-10-27T08:00:00Z"},
    )
    assert mark_res.status_code == 200
    assert mark_res.json()["status"] == "eaten"

    # Future date has untouched items (not eaten)
    # Now update pin for breakfast
    await client.put(
        "/nutrition/pinned-meals/breakfast",
        json={
            "active": True,
            "items": [{"title": "Млинці з сиром", "portion_g": 200, "kcal": 410.0}],
        },
    )

    # Today had an eaten meal -> it remains untouched!
    today_res2 = await client.get(f"/nutrition/menus/{today}")
    today_b = next(m for m in today_res2.json()["menus"] if m["meal_type"] == "breakfast")
    assert len(today_b["items"]) == 1
    assert today_b["items"][0]["title"] == "Сирники"
    assert today_b["status"] == "eaten"

    # Future was not eaten -> resynced to new pin!
    fut_res2 = await client.get(f"/nutrition/menus/{future}")
    fut_b = next(m for m in fut_res2.json()["menus"] if m["meal_type"] == "breakfast")
    assert len(fut_b["items"]) == 1
    assert fut_b["items"][0]["title"] == "Млинці з сиром"
    assert fut_b["items"][0]["kcal"] == 410.0


@pytest.mark.asyncio
async def test_cross_pinned_meal_and_available_pins_and_restore(client: AsyncClient):
    await init_profile(client)
    today = dt_date.today()

    # Create active pin for breakfast
    await client.put(
        "/nutrition/pinned-meals/breakfast",
        json={
            "active": True,
            "items": [{"title": "Гречка", "portion_g": 200, "kcal": 220.0}],
        },
    )

    # Materialize on today
    read1 = await client.get(f"/nutrition/menus/{today}")
    assert read1.status_code == 200
    b1 = next(m for m in read1.json()["menus"] if m["meal_type"] == "breakfast")
    assert b1["pinned"] is True
    assert len(b1["items"]) == 1
    assert not any(p["meal_type"] == "breakfast" for p in read1.json()["available_pins"])

    # 1. First cross: DELETE /menus/{date}/meals/{meal_type}
    del_meal = await client.delete(f"/nutrition/menus/{today}/meals/breakfast")
    assert del_meal.status_code == 204

    # GET: meal remains active, items deleted, pinned is false, appears in available_pins
    read2 = await client.get(f"/nutrition/menus/{today}")
    assert read2.status_code == 200
    b2 = next(m for m in read2.json()["menus"] if m["meal_type"] == "breakfast")
    assert b2["pinned"] is False
    assert b2["items"] == []
    assert any(p["meal_type"] == "breakfast" for p in read2.json()["available_pins"])

    # 2. It does NOT re-materialize on subsequent read
    read3 = await client.get(f"/nutrition/menus/{today}")
    b3 = next(m for m in read3.json()["menus"] if m["meal_type"] == "breakfast")
    assert b3["pinned"] is False
    assert b3["items"] == []

    # 3. Restore endpoint: POST /menus/{date}/meals/{meal_type}/pin
    restore_resp = await client.post(f"/nutrition/menus/{today}/meals/breakfast/pin")
    assert restore_resp.status_code == 200
    restored = restore_resp.json()
    assert restored["pinned"] is True
    assert len(restored["items"]) == 1
    assert restored["items"][0]["title"] == "Гречка"

    # Now verify GET shows it pinned and not in available_pins
    read4 = await client.get(f"/nutrition/menus/{today}")
    assert not any(p["meal_type"] == "breakfast" for p in read4.json()["available_pins"])

    # 4. Cross again, then second cross removes meal completely
    await client.delete(f"/nutrition/menus/{today}/meals/breakfast")
    del2 = await client.delete(f"/nutrition/menus/{today}/meals/breakfast")
    assert del2.status_code == 204

    read5 = await client.get(f"/nutrition/menus/{today}")
    # Meal is removed from canvas
    assert not any(m["meal_type"] == "breakfast" for m in read5.json()["menus"])
    # But because active pin exists, it still appears in available_pins
    assert any(p["meal_type"] == "breakfast" for p in read5.json()["available_pins"])

    # Restore from removed tombstone state
    restore_resp2 = await client.post(f"/nutrition/menus/{today}/meals/breakfast/pin")
    assert restore_resp2.status_code == 200
    assert restore_resp2.json()["pinned"] is True

    read6 = await client.get(f"/nutrition/menus/{today}")
    assert any(m["meal_type"] == "breakfast" for m in read6.json()["menus"])


@pytest.mark.asyncio
async def test_restore_preserves_marked_items(client: AsyncClient):
    await init_profile(client)
    today = dt_date.today()

    await client.put(
        "/nutrition/pinned-meals/breakfast",
        json={
            "active": True,
            "items": [
                {"title": "Омлет", "portion_g": 150, "kcal": 220.0, "position": 1},
                {"title": "Тост", "portion_g": 50, "kcal": 130.0, "position": 2},
            ],
        },
    )

    read1 = await client.get(f"/nutrition/menus/{today}")
    b1 = next(m for m in read1.json()["menus"] if m["meal_type"] == "breakfast")
    b1_id = b1["id"]

    # Mark Breakfast as eaten
    await client.patch(
        f"/nutrition/menus/{b1_id}",
        json={"eaten_at": "2026-10-27T08:00:00Z"},
    )

    # Calling restore on an eaten meal is refused with 409
    refused_resp = await client.post(f"/nutrition/menus/{today}/meals/breakfast/pin")
    assert refused_resp.status_code == 409

    # Unmark meal
    await client.patch(
        f"/nutrition/menus/{b1_id}",
        json={"eaten_at": None},
    )

    # Add a manual item
    await client.post(
        f"/nutrition/menus/{today}/items?meal_type=breakfast",
        json={"title": "Апельсин", "portion_g": 100, "kcal": 45.0},
    )

    # Call restore now succeeds
    restore_resp = await client.post(f"/nutrition/menus/{today}/meals/breakfast/pin")
    assert restore_resp.status_code == 200
    items = restore_resp.json()["items"]
    assert len(items) == 2
    titles = [it["title"] for it in items]
    assert "Омлет" in titles
    assert "Тост" in titles
    assert "Апельсин" not in titles


def test_generation_flags_and_generatability_with_pinned_meal():
    pinned_meal = MenuRead(
        user_id="01940000-0000-7000-8000-000000000001",
        date=dt_date(2026, 10, 27),
        meal_type=MealType.BREAKFAST,
        time="08:00",
        status=MenuStatus.PLANNED,
        pinned=True,
        items=[],
    )
    # Pinned meal is not generatable
    assert is_meal_generatable(pinned_meal) is False

    empty_meal = MenuRead(
        user_id="01940000-0000-7000-8000-000000000001",
        date=dt_date(2026, 10, 27),
        meal_type=MealType.LUNCH,
        time="13:00",
        status=MenuStatus.PLANNED,
        pinned=False,
        items=[],
    )
    assert is_meal_generatable(empty_meal) is True

    # Day with only pinned meal: cannot generate
    can_gen, replaces_manual = compute_day_generation_flags([pinned_meal])
    assert can_gen is False
    assert replaces_manual is False

    # Day with pinned meal and empty meal: can generate, doesn't replace manual
    can_gen2, replaces_manual2 = compute_day_generation_flags([pinned_meal, empty_meal])
    assert can_gen2 is True
    assert replaces_manual2 is False


@pytest.mark.asyncio
async def test_generation_leaves_pinned_meal_untouched_and_counts_it_as_context(
    client: AsyncClient,
):
    await init_profile(client)
    today = dt_date.today()

    # Create dish for Obid
    dish_resp = await client.post(
        "/nutrition/dishes",
        json={
            "name": "Борщ український",
            "kind": "ready",
            "cooked_weight_g": 1000,
            "cooked_on": str(today),
        },
    )
    assert dish_resp.status_code == 201
    dish_id = dish_resp.json()["id"]

    # Create pin for breakfast (without kcal initially to test estimator during generation)
    await client.put(
        "/nutrition/pinned-meals/breakfast",
        json={
            "active": True,
            "items": [{"title": "Вівсянка з ягодами", "portion_g": 250}],
        },
    )

    # Mock Gemini for estimate and menu generation
    def fake_generate_json(prompt: str, **kwargs):
        if "оцінити" in prompt:
            # Estimator called
            item_id = kwargs["response_schema"]["properties"]["items"]["items"]["properties"][
                "id"
            ]["enum"][0]
            return GeminiResult(
                model="gemini-2.0-flash",
                data={
                    "items": [
                        {
                            "id": item_id,
                            "kcal": 310.0,
                            "protein_g": 10.0,
                            "fat_g": 5.0,
                            "carbs_g": 50.0,
                            "fiber_g": 4.0,
                            "sugar_g": 2.0,
                            "sodium_mg": 10.0,
                        }
                    ]
                },
                raw_text="{}",
                input_tokens=100,
                output_tokens=50,
            )
        # Menu generator called
        # Assert that prompt includes existing pinned breakfast with its estimated kcal!
        assert "Сніданок" in prompt
        assert "Вівсянка з ягодами" in prompt
        return GeminiResult(
            model="gemini-2.0-flash",
            data={
                "rationale": "Збалансоване меню з урахуванням закріпленого сніданку",
                "shopping_list": [],
                "items": [
                    # Model tries to propose items for Obid and even Breakfast
                    {
                        "meal_type": "breakfast",
                        "dish_id": dish_id,
                        "title": "Борщ український",
                        "portion_g": 300,
                        "portion_text": "300 г",
                        "position": 1,
                    },
                    {
                        "meal_type": "lunch",
                        "dish_id": dish_id,
                        "title": "Борщ український",
                        "portion_g": 350,
                        "portion_text": "350 г",
                        "position": 1,
                    },
                ],
            },
            raw_text="{}",
            input_tokens=200,
            output_tokens=100,
        )

    with (
        patch(
            "nutrition.service.generator.gemini_infra.generate_json",
            side_effect=fake_generate_json,
        ),
        patch(
            "nutrition.service.estimator.gemini_infra.generate_json",
            side_effect=fake_generate_json,
        ),
    ):
        gen_resp = await client.post(
            f"/nutrition/menus/{today}/generate",
            json={"scope": "day"},
        )
        assert gen_resp.status_code == 200

    day_menus = gen_resp.json()["menus"]
    breakfast = next(m for m in day_menus if m["meal_type"] == "breakfast")
    obid = next(m for m in day_menus if m["meal_type"] == "lunch")

    # Breakfast remained pinned and untouched!
    assert breakfast["pinned"] is True
    assert len(breakfast["items"]) == 1
    assert breakfast["items"][0]["title"] == "Вівсянка з ягодами"
    # Its kcal was estimated by estimator
    assert breakfast["items"][0]["kcal"] == 310.0

    # Obid was generated with Borshch
    assert obid["pinned"] is False
    assert len(obid["items"]) == 1
    assert obid["items"][0]["title"] == "Борщ український"
