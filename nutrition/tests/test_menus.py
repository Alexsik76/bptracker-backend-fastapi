from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from gemini_infra.types import GeminiResult


@pytest.mark.asyncio
async def test_get_menu_not_found(client: AsyncClient):
    resp = await client.get("/nutrition/menus/2026-10-23")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Menu not found"


@pytest.mark.asyncio
async def test_add_manual_item_with_dish(client: AsyncClient):
    # Create ready dish
    dish_resp = await client.post(
        "/nutrition/dishes",
        json={
            "name": "Гречка відварена",
            "kind": "ready",
            "cooked_weight_g": 500,
            "cooked_on": "2026-10-23",
        },
    )
    assert dish_resp.status_code == 201
    dish_id = dish_resp.json()["id"]

    # Add item without title -> should take dish name
    item_resp = await client.post(
        "/nutrition/menus/2026-10-23/items?slot=Обід",
        json={
            "dish_id": dish_id,
            "portion_g": 200,
            "position": 1,
        },
    )
    assert item_resp.status_code == 201
    item_data = item_resp.json()
    assert item_data["title"] == "Гречка відварена"
    assert item_data["origin"] == "manual"
    assert item_data["portion_g"] == 200
    assert item_data["dish_id"] == dish_id

    # Verify menu GET returns list of meals for the day
    menu_resp = await client.get("/nutrition/menus/2026-10-23")
    assert menu_resp.status_code == 200
    day_data = menu_resp.json()
    assert day_data["date"] == "2026-10-23"
    day_menus = day_data["menus"]
    assert len(day_menus) == 1
    meal = day_menus[0]
    assert meal["date"] == "2026-10-23"
    assert meal["slot"] == "Обід"
    assert meal["status"] == "planned"
    assert len(meal["items"]) == 1
    assert meal["items"][0]["id"] == item_data["id"]


@pytest.mark.asyncio
async def test_add_manual_item_with_product(client: AsyncClient):
    # Create product
    prod_resp = await client.post(
        "/nutrition/products",
        json={
            "name": "Яблуко Голден",
            "category": "Фрукти",
        },
    )
    assert prod_resp.status_code == 201
    prod_id = prod_resp.json()["id"]

    # Add item with custom title override
    item_resp = await client.post(
        "/nutrition/menus/2026-10-23/items?slot=Перекус",
        json={
            "product_id": prod_id,
            "title": "Свіже яблуко",
            "portion_g": 150,
            "portion_text": "1 шт",
        },
    )
    assert item_resp.status_code == 201
    item_data = item_resp.json()
    assert item_data["title"] == "Свіже яблуко"
    assert item_data["product_id"] == prod_id
    assert item_data["portion_text"] == "1 шт"


@pytest.mark.asyncio
async def test_ready_dish_stock_and_meal_lifecycle(client: AsyncClient):
    # 1. Create ready dish with 600g cooked
    dish_resp = await client.post(
        "/nutrition/dishes",
        json={
            "name": "Запечене філе індички",
            "kind": "ready",
            "cooked_weight_g": 600,
            "cooked_on": "2026-10-23",
        },
    )
    dish_id = dish_resp.json()["id"]
    assert dish_resp.json()["remaining_g"] == 600

    # 2. Add manual item 200g into Lunch
    item_resp = await client.post(
        "/nutrition/menus/2026-10-23/items?slot=Обід",
        json={
            "dish_id": dish_id,
            "portion_g": 200,
        },
    )
    item_id = item_resp.json()["id"]
    menu_id = item_resp.json()["menu_id"]

    # Remaining should still be 600g because meal is planned, not eaten
    dish_check = await client.get(f"/nutrition/dishes/{dish_id}")
    assert dish_check.json()["remaining_g"] == 600

    # 3. Mark meal eaten
    patch_meal = await client.patch(
        f"/nutrition/menus/{menu_id}",
        json={"status": "eaten"},
    )
    assert patch_meal.status_code == 200
    assert patch_meal.json()["status"] == "eaten"
    assert patch_meal.json()["eaten_at"] is not None

    # Dish remaining should now be 400g (600 - 200)
    dish_check = await client.get(f"/nutrition/dishes/{dish_id}")
    assert dish_check.json()["remaining_g"] == 400

    # 4. Update portion while eaten: 200g -> 250g
    patch_resp = await client.patch(
        f"/nutrition/menu-items/{item_id}",
        json={"portion_g": 250},
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["portion_g"] == 250

    # Dish remaining should now be 350g (400 - 50)
    dish_check = await client.get(f"/nutrition/dishes/{dish_id}")
    assert dish_check.json()["remaining_g"] == 350

    # 5. Clear status (back to planned)
    patch_meal = await client.patch(
        f"/nutrition/menus/{menu_id}",
        json={"status": "planned"},
    )
    assert patch_meal.status_code == 200
    assert patch_meal.json()["status"] == "planned"
    assert patch_meal.json()["eaten_at"] is None

    # Dish remaining should be restored back to 600g
    dish_check = await client.get(f"/nutrition/dishes/{dish_id}")
    assert dish_check.json()["remaining_g"] == 600

    # 6. Mark eaten again, then delete item -> remaining restored
    await client.patch(
        f"/nutrition/menus/{menu_id}",
        json={"status": "eaten"},
    )
    dish_check = await client.get(f"/nutrition/dishes/{dish_id}")
    assert dish_check.json()["remaining_g"] == 350

    del_resp = await client.delete(f"/nutrition/menu-items/{item_id}")
    assert del_resp.status_code == 204

    dish_check = await client.get(f"/nutrition/dishes/{dish_id}")
    assert dish_check.json()["remaining_g"] == 600


@pytest.mark.asyncio
async def test_item_replacement_stock_correction(client: AsyncClient):
    # Test review item 1.2: replacing dish A with dish B in an eaten meal
    dish_a = (
        await client.post(
            "/nutrition/dishes",
            json={
                "name": "Страва A",
                "kind": "ready",
                "cooked_weight_g": 500,
                "cooked_on": "2026-10-23",
            },
        )
    ).json()
    dish_b = (
        await client.post(
            "/nutrition/dishes",
            json={
                "name": "Страва B",
                "kind": "ready",
                "cooked_weight_g": 500,
                "cooked_on": "2026-10-23",
            },
        )
    ).json()

    # Add Dish A (200g) into Lunch and mark eaten
    item_resp = await client.post(
        "/nutrition/menus/2026-10-23/items?slot=Обід",
        json={"dish_id": dish_a["id"], "portion_g": 200},
    )
    item_id = item_resp.json()["id"]
    menu_id = item_resp.json()["menu_id"]

    await client.patch(f"/nutrition/menus/{menu_id}", json={"status": "eaten"})
    assert (await client.get(f"/nutrition/dishes/{dish_a['id']}")).json()["remaining_g"] == 300
    assert (await client.get(f"/nutrition/dishes/{dish_b['id']}")).json()["remaining_g"] == 500

    # Replace Dish A with Dish B (200g)
    await client.patch(f"/nutrition/menu-items/{item_id}", json={"dish_id": dish_b["id"]})

    # A should be restored to 500g, B should be deducted to 300g
    assert (await client.get(f"/nutrition/dishes/{dish_a['id']}")).json()["remaining_g"] == 500
    assert (await client.get(f"/nutrition/dishes/{dish_b['id']}")).json()["remaining_g"] == 300


@pytest.mark.asyncio
async def test_meal_becomes_incomplete_when_dish_exhausted_or_deleted(client: AsyncClient):
    # User's scenario:
    # Boiled potatoes: 500g
    # Lunch: 200g, Dinner: 200g
    potato = (
        await client.post(
            "/nutrition/dishes",
            json={
                "name": "Картопля варена",
                "kind": "ready",
                "cooked_weight_g": 500,
                "cooked_on": "2026-10-23",
            },
        )
    ).json()
    p_id = potato["id"]

    # Form lunch (200g) and dinner (200g)
    lunch_item = (
        await client.post(
            "/nutrition/menus/2026-10-23/items?slot=Обід",
            json={"dish_id": p_id, "portion_g": 200},
        )
    ).json()
    await client.post(
        "/nutrition/menus/2026-10-23/items?slot=Вечеря",
        json={"dish_id": p_id, "portion_g": 200},
    )

    # Both lunch and dinner are planned
    menus = (await client.get("/nutrition/menus/2026-10-23")).json()["menus"]
    assert all(m["status"] == "planned" for m in menus)

    # Eat lunch -> remaining becomes 300g
    await client.patch(f"/nutrition/menus/{lunch_item['menu_id']}", json={"status": "eaten"})
    assert (await client.get(f"/nutrition/dishes/{p_id}")).json()["remaining_g"] == 300

    # User manually corrects remaining potatoes to 0 (dropped on floor)
    await client.patch(f"/nutrition/dishes/{p_id}", json={"remaining_g": 0})

    # Verify:
    # 1. Lunch (eaten) remains eaten with 200g!
    # 2. Dinner (planned) now becomes incomplete!
    menus = (await client.get("/nutrition/menus/2026-10-23")).json()["menus"]
    menu_by_slot = {m["slot"]: m for m in menus}
    assert menu_by_slot["Обід"]["status"] == "eaten"
    assert menu_by_slot["Вечеря"]["status"] == "incomplete"

    # Deleting potato dish leaves Lunch eaten with title, Dinner incomplete
    await client.delete(f"/nutrition/dishes/{p_id}")
    menus = (await client.get("/nutrition/menus/2026-10-23")).json()["menus"]
    menu_by_slot = {m["slot"]: m for m in menus}
    assert menu_by_slot["Обід"]["status"] == "eaten"
    assert menu_by_slot["Обід"]["items"][0]["dish_id"] is None
    assert menu_by_slot["Обід"]["items"][0]["title"] == "Картопля варена"
    assert menu_by_slot["Вечеря"]["status"] == "incomplete"


@pytest.mark.asyncio
async def test_menu_user_isolation(client_factory, make_user):
    user_a = await make_user("menu_a@example.com")
    user_b = await make_user("menu_b@example.com")

    client1 = client_factory(user_a)
    prod_resp = await client1.post(
        "/nutrition/products",
        json={"name": "Сир кисломолочний 5%"},
    )
    prod_id = prod_resp.json()["id"]

    item_resp = await client1.post(
        "/nutrition/menus/2026-10-23/items?slot=Сніданок",
        json={
            "product_id": prod_id,
            "portion_g": 150,
        },
    )
    item_id = item_resp.json()["id"]
    menu_id = item_resp.json()["menu_id"]

    client2 = client_factory(user_b)
    # User 2 cannot see user 1's menu
    u2_menu = await client2.get("/nutrition/menus/2026-10-23")
    assert u2_menu.status_code == 404

    # User 2 cannot patch user 1's meal
    u2_patch_menu = await client2.patch(
        f"/nutrition/menus/{menu_id}",
        json={"status": "eaten"},
    )
    assert u2_patch_menu.status_code == 404

    # User 2 cannot patch user 1's item
    u2_patch = await client2.patch(
        f"/nutrition/menu-items/{item_id}",
        json={"portion_g": 200},
    )
    assert u2_patch.status_code == 404

    # User 2 cannot delete user 1's item
    u2_del = await client2.delete(f"/nutrition/menu-items/{item_id}")
    assert u2_del.status_code == 404


@pytest.mark.asyncio
async def test_per_item_mark_unmark_with_stock(client: AsyncClient):
    dish = (
        await client.post(
            "/nutrition/dishes",
            json={
                "name": "Запечене філе індички",
                "kind": "ready",
                "cooked_weight_g": 500,
                "cooked_on": "2026-10-26",
            },
        )
    ).json()
    dish_id = dish["id"]

    item = (
        await client.post(
            "/nutrition/menus/2026-10-26/items?slot=Обід",
            json={"dish_id": dish_id, "portion_g": 200},
        )
    ).json()
    item_id = item["id"]

    # Initially unmarked, remaining_g is 500
    assert item["status"] is None
    assert item["eaten_at"] is None
    assert (await client.get(f"/nutrition/dishes/{dish_id}")).json()["remaining_g"] == 500

    # Mark item eaten without eaten_at -> defaults to now (UTC)
    patch1 = (
        await client.patch(
            f"/nutrition/menu-items/{item_id}",
            json={"status": "eaten"},
        )
    ).json()
    assert patch1["status"] == "eaten"
    assert patch1["eaten_at"] is not None
    assert (await client.get(f"/nutrition/dishes/{dish_id}")).json()["remaining_g"] == 300

    # Meal is now eaten
    menu = (await client.get("/nutrition/menus/2026-10-26")).json()["menus"][0]
    assert menu["status"] == "eaten"
    assert menu["eaten_at"] == patch1["eaten_at"]

    # Explicit null unmarks and clears eaten_at
    patch2 = (
        await client.patch(
            f"/nutrition/menu-items/{item_id}",
            json={"status": None},
        )
    ).json()
    assert patch2["status"] is None
    assert patch2["eaten_at"] is None
    assert (await client.get(f"/nutrition/dishes/{dish_id}")).json()["remaining_g"] == 500

    # Meal is back to planned
    menu = (await client.get("/nutrition/menus/2026-10-26")).json()["menus"][0]
    assert menu["status"] == "planned"
    assert menu["eaten_at"] is None

    # Mark item skipped -> clears eaten_at, does not affect stock
    patch3 = (
        await client.patch(
            f"/nutrition/menu-items/{item_id}",
            json={"status": "skipped"},
        )
    ).json()
    assert patch3["status"] == "skipped"
    assert patch3["eaten_at"] is None
    assert (await client.get(f"/nutrition/dishes/{dish_id}")).json()["remaining_g"] == 500

    menu = (await client.get("/nutrition/menus/2026-10-26")).json()["menus"][0]
    assert menu["status"] == "skipped"

    # Mark eaten with explicit eaten_at
    fixed_time = "2026-10-26T12:30:00+00:00"
    patch4 = (
        await client.patch(
            f"/nutrition/menu-items/{item_id}",
            json={"status": "eaten", "eaten_at": fixed_time},
        )
    ).json()
    assert patch4["status"] == "eaten"
    assert patch4["eaten_at"] == "2026-10-26T12:30:00Z"
    assert (await client.get(f"/nutrition/dishes/{dish_id}")).json()["remaining_g"] == 300


@pytest.mark.asyncio
async def test_bulk_meal_patch(client: AsyncClient):
    dish = (
        await client.post(
            "/nutrition/dishes",
            json={
                "name": "Плов з куркою",
                "kind": "ready",
                "cooked_weight_g": 600,
                "cooked_on": "2026-10-26",
            },
        )
    ).json()
    prod = (
        await client.post(
            "/nutrition/products",
            json={"name": "Огірок свіжий", "category": "Овочі"},
        )
    ).json()

    # Add 2 items to Lunch
    item1 = (
        await client.post(
            "/nutrition/menus/2026-10-26/items?slot=Обід",
            json={"dish_id": dish["id"], "portion_g": 250},
        )
    ).json()
    item2 = (
        await client.post(
            "/nutrition/menus/2026-10-26/items?slot=Обід",
            json={"product_id": prod["id"], "portion_g": 100},
        )
    ).json()
    menu_id = item1["menu_id"]

    # Bulk mark meal eaten
    meal_eaten = (
        await client.patch(
            f"/nutrition/menus/{menu_id}",
            json={"status": "eaten"},
        )
    ).json()
    assert meal_eaten["status"] == "eaten"
    assert meal_eaten["eaten_at"] is not None
    assert all(it["status"] == "eaten" for it in meal_eaten["items"])
    assert (await client.get(f"/nutrition/dishes/{dish['id']}")).json()["remaining_g"] == 350

    # Bulk mark meal planned (unmark all items)
    meal_planned = (
        await client.patch(
            f"/nutrition/menus/{menu_id}",
            json={"status": "planned"},
        )
    ).json()
    assert meal_planned["status"] == "planned"
    assert meal_planned["eaten_at"] is None
    assert all(it["status"] is None for it in meal_planned["items"])
    assert (await client.get(f"/nutrition/dishes/{dish['id']}")).json()["remaining_g"] == 600

    # Mark one item eaten -> meal status becomes partial
    await client.patch(f"/nutrition/menu-items/{item1['id']}", json={"status": "eaten"})
    menu_resp = (await client.get("/nutrition/menus/2026-10-26")).json()["menus"][0]
    assert menu_resp["status"] == "partial"

    # Mark second item skipped -> mixed eaten + skipped -> status is partial
    await client.patch(f"/nutrition/menu-items/{item2['id']}", json={"status": "skipped"})
    menu_resp2 = (await client.get("/nutrition/menus/2026-10-26")).json()["menus"][0]
    assert menu_resp2["status"] == "partial"

    # Bulk mark skipped
    meal_skipped = (
        await client.patch(
            f"/nutrition/menus/{menu_id}",
            json={"status": "skipped"},
        )
    ).json()
    assert meal_skipped["status"] == "skipped"
    assert all(it["status"] == "skipped" for it in meal_skipped["items"])


@pytest.mark.asyncio
async def test_swap_dish_on_eaten_item(client: AsyncClient):
    dish_a = (
        await client.post(
            "/nutrition/dishes",
            json={
                "name": "Страва A",
                "kind": "ready",
                "cooked_weight_g": 500,
                "cooked_on": "2026-10-26",
            },
        )
    ).json()
    dish_b = (
        await client.post(
            "/nutrition/dishes",
            json={
                "name": "Страва B",
                "kind": "ready",
                "cooked_weight_g": 400,
                "cooked_on": "2026-10-26",
            },
        )
    ).json()

    # Add item with dish A, portion 150g, and mark eaten directly via item patch
    item = (
        await client.post(
            "/nutrition/menus/2026-10-26/items?slot=Обід",
            json={"dish_id": dish_a["id"], "portion_g": 150},
        )
    ).json()
    await client.patch(f"/nutrition/menu-items/{item['id']}", json={"status": "eaten"})

    assert (await client.get(f"/nutrition/dishes/{dish_a['id']}")).json()["remaining_g"] == 350
    assert (await client.get(f"/nutrition/dishes/{dish_b['id']}")).json()["remaining_g"] == 400

    # Swap dish A for dish B on the eaten item and change portion to 200g
    patch_swap = await client.patch(
        f"/nutrition/menu-items/{item['id']}",
        json={"dish_id": dish_b["id"], "portion_g": 200},
    )
    assert patch_swap.status_code == 200
    assert patch_swap.json()["dish_id"] == dish_b["id"]
    assert patch_swap.json()["portion_g"] == 200
    assert patch_swap.json()["status"] == "eaten"

    # Dish A should be restored (+150g = 500g), Dish B should be deducted (-200g = 200g)
    assert (await client.get(f"/nutrition/dishes/{dish_a['id']}")).json()["remaining_g"] == 500
    assert (await client.get(f"/nutrition/dishes/{dish_b['id']}")).json()["remaining_g"] == 200


@pytest.mark.asyncio
async def test_unmarked_meal_on_previous_date_does_not_make_today_incomplete(
    client: AsyncClient,
):
    # Dish with 200g cooked
    dish = (
        await client.post(
            "/nutrition/dishes",
            json={
                "name": "Суп овочевий",
                "kind": "ready",
                "cooked_weight_g": 200,
                "cooked_on": "2026-10-25",
            },
        )
    ).json()

    # Yesterday (2026-10-25): Unmarked item with 200g
    await client.post(
        "/nutrition/menus/2026-10-25/items?slot=Обід",
        json={"dish_id": dish["id"], "portion_g": 200},
    )

    # Today (2026-10-26): Unmarked item with 200g
    await client.post(
        "/nutrition/menus/2026-10-26/items?slot=Обід",
        json={"dish_id": dish["id"], "portion_g": 200},
    )

    # Remaining in DB is still 200g because neither item is eaten
    assert (await client.get(f"/nutrition/dishes/{dish['id']}")).json()["remaining_g"] == 200

    # Today's menu must be PLANNED (not incomplete):
    # yesterday's unmarked item is not considered
    today_menu_resp = await client.get("/nutrition/menus/2026-10-26")
    assert today_menu_resp.status_code == 200
    today_meal = today_menu_resp.json()["menus"][0]
    assert today_meal["status"] == "planned"
    assert today_meal["items"][0]["incomplete"] is False


@pytest.mark.asyncio
async def test_generation_keeps_marked_items_of_partially_marked_slot(client: AsyncClient):
    # Setup profile with meals
    await client.put(
        "/nutrition/profile",
        json={
            "sex": "male",
            "birth_year": 1980,
            "height_cm": 180,
            "weight_kg": 85.0,
            "target_weight_kg": 80.0,
            "pace_kg_per_week": 0.5,
            "meals": [
                {"name": "Сніданок", "time": "08:00"},
                {"name": "Обід", "time": "13:00"},
                {"name": "Вечеря", "time": "19:00"},
            ],
        },
    )
    dish = (
        await client.post(
            "/nutrition/dishes",
            json={
                "name": "Індичка запечена",
                "kind": "ready",
                "cooked_weight_g": 600,
                "cooked_on": "2026-10-26",
            },
        )
    ).json()

    # Lunch has 2 items:
    # 1. marked eaten (portion 100g)
    # 2. unmarked (portion 100g)
    item1 = (
        await client.post(
            "/nutrition/menus/2026-10-26/items?slot=Обід",
            json={"dish_id": dish["id"], "portion_g": 100, "position": 1},
        )
    ).json()
    await client.patch(f"/nutrition/menu-items/{item1['id']}", json={"status": "eaten"})

    item2 = (
        await client.post(
            "/nutrition/menus/2026-10-26/items?slot=Обід",
            json={"dish_id": dish["id"], "portion_g": 100, "position": 2},
        )
    ).json()

    mock_gemini = GeminiResult(
        data={
            "rationale": "Оновлення обіду",
            "shopping_list": [],
            "items": [
                {
                    "slot": "Обід",
                    "dish_id": dish["id"],
                    "product_id": None,
                    "title": "Індичка запечена",
                    "portion_g": 200,
                    "portion_text": "200 г",
                    "kcal_est": 260,
                    "position": 1,
                }
            ],
        },
        raw_text="{}",
        model="gemini-2.0-flash",
        input_tokens=500,
        output_tokens=100,
    )

    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_gen:
        mock_gen.return_value = mock_gemini
        gen_resp = await client.post(
            "/nutrition/menus/2026-10-26/generate",
            json={"scope": "slot", "slot": "Обід"},
        )
        assert gen_resp.status_code == 200

    # Verify Lunch items
    menu_resp = await client.get("/nutrition/menus/2026-10-26")
    lunch = [m for m in menu_resp.json()["menus"] if m["slot"] == "Обід"][0]
    items = lunch["items"]

    # Must contain 2 items: item1 (eaten, kept) and new generated item
    assert len(items) == 2
    assert items[0]["id"] == item1["id"]
    assert items[0]["status"] == "eaten"
    assert items[0]["portion_g"] == 100
    assert items[0]["position"] == 1

    # item2 was replaced; new item has position after marked items (position 2)
    assert items[1]["id"] != item2["id"]
    assert items[1]["portion_g"] == 200
    assert items[1]["position"] == 2
    assert items[1]["status"] is None


@pytest.mark.asyncio
async def test_generated_slot_outside_profile_rejected_422(client: AsyncClient):
    await client.put(
        "/nutrition/profile",
        json={
            "sex": "female",
            "birth_year": 1990,
            "height_cm": 165,
            "weight_kg": 60.0,
            "target_weight_kg": 55.0,
            "pace_kg_per_week": 0.5,
            "meals": [
                {"name": "Сніданок", "time": "08:00"},
                {"name": "Обід", "time": "13:00"},
                {"name": "Вечеря", "time": "19:00"},
            ],
        },
    )
    dish = (
        await client.post(
            "/nutrition/dishes",
            json={
                "name": "Омлет",
                "kind": "ready",
                "cooked_weight_g": 300,
                "cooked_on": "2026-10-26",
            },
        )
    ).json()

    # Gemini returns an invalid slot name "Полуденок" not in profile schedule
    mock_gemini = GeminiResult(
        data={
            "rationale": "Некоректний слот",
            "shopping_list": [],
            "items": [
                {
                    "slot": "Полуденок",
                    "dish_id": dish["id"],
                    "product_id": None,
                    "title": "Омлет",
                    "portion_g": 150,
                    "portion_text": "150 г",
                    "kcal_est": 200,
                    "position": 1,
                }
            ],
        },
        raw_text="{}",
        model="gemini-2.0-flash",
        input_tokens=500,
        output_tokens=100,
    )

    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_gen:
        mock_gen.return_value = mock_gemini
        gen_resp = await client.post(
            "/nutrition/menus/2026-10-26/generate",
            json={"scope": "day"},
        )
        assert gen_resp.status_code == 422
        assert "not allowed in profile schedule" in gen_resp.json()["detail"]
