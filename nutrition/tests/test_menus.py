from datetime import date as dt_date
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from gemini_infra.types import GeminiResult
from nutrition.models import MealType, Menu, MenuItemCreate
from users.models import User


@pytest.mark.asyncio
async def test_get_menu_not_found(client: AsyncClient):
    resp = await client.get("/nutrition/menus/2026-10-23")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Menu not found"


@pytest.mark.asyncio
async def test_add_manual_item_with_dish(client: AsyncClient):
    test_date = (dt_date.today() + timedelta(days=2)).isoformat()
    await client.post(f"/nutrition/menus/{test_date}/create")
    # Create ready dish
    dish_resp = await client.post(
        "/nutrition/dishes",
        json={
            "name": "Гречка відварена",
            "kind": "ready",
            "cooked_weight_g": 500,
            "cooked_on": test_date,
        },
    )
    assert dish_resp.status_code == 201
    dish_id = dish_resp.json()["id"]

    # Add item without title -> should take dish name
    item_resp = await client.post(
        f"/nutrition/menus/{test_date}/items?meal_type=lunch",
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
    menu_resp = await client.get(f"/nutrition/menus/{test_date}")
    assert menu_resp.status_code == 200
    day_data = menu_resp.json()
    assert day_data["date"] == test_date
    day_menus = day_data["menus"]
    lunch = next(m for m in day_menus if m["meal_type"] == "lunch")
    assert lunch["date"] == test_date
    assert lunch["meal_type"] == "lunch"
    assert lunch["status"] == "planned"
    assert len(lunch["items"]) == 1
    assert lunch["items"][0]["id"] == item_data["id"]


@pytest.mark.asyncio
async def test_add_manual_item_with_dish_custom_title(client: AsyncClient):
    await client.post("/nutrition/menus/2026-10-23/create")
    # Create dish
    dish_resp = await client.post(
        "/nutrition/dishes",
        json={
            "name": "Яблуко Голден",
            "kind": "ready",
            "cooked_weight_g": 500,
            "cooked_on": "2026-10-23",
        },
    )
    assert dish_resp.status_code == 201
    dish_id = dish_resp.json()["id"]

    # Add item with custom title override
    item_resp = await client.post(
        "/nutrition/menus/2026-10-23/items?meal_type=morning_snack",
        json={
            "dish_id": dish_id,
            "title": "Свіже яблуко",
            "portion_g": 150,
            "portion_text": "1 шт",
        },
    )
    assert item_resp.status_code == 201
    item_data = item_resp.json()
    assert item_data["title"] == "Свіже яблуко"
    assert item_data["dish_id"] == dish_id
    assert item_data["portion_text"] == "1 шт"


@pytest.mark.asyncio
async def test_ready_dish_stock_and_meal_lifecycle(client: AsyncClient):
    test_date = (dt_date.today() + timedelta(days=2)).isoformat()
    # 1. Create ready dish with 600g cooked
    dish_resp = await client.post(
        "/nutrition/dishes",
        json={
            "name": "Запечене філе індички",
            "kind": "ready",
            "cooked_weight_g": 600,
            "cooked_on": test_date,
        },
    )
    dish_id = dish_resp.json()["id"]
    assert dish_resp.json()["remaining_g"] == 600

    await client.post(f"/nutrition/menus/{test_date}/create")

    # 2. Add manual item 200g into Lunch
    item_resp = await client.post(
        f"/nutrition/menus/{test_date}/items?meal_type=lunch",
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
        json={"eaten_at": f"{test_date}T12:00:00Z"},
    )
    assert patch_meal.status_code == 200
    assert patch_meal.json()["status"] == "eaten"
    assert patch_meal.json()["eaten_at"] is not None

    # Dish remaining should now be 400g (600 - 200)
    dish_check = await client.get(f"/nutrition/dishes/{dish_id}")
    assert dish_check.json()["remaining_g"] == 400

    # 4. Updating portion while eaten is refused with 409
    patch_refused = await client.patch(
        f"/nutrition/menu-items/{item_id}",
        json={"portion_g": 250},
    )
    assert patch_refused.status_code == 409

    # Dish remaining remains 400g
    dish_check = await client.get(f"/nutrition/dishes/{dish_id}")
    assert dish_check.json()["remaining_g"] == 400

    # 5. Clear status (back to planned) -> stock restored to 600g
    patch_meal = await client.patch(
        f"/nutrition/menus/{menu_id}",
        json={"eaten_at": None},
    )
    assert patch_meal.status_code == 200
    assert patch_meal.json()["status"] == "planned"
    assert patch_meal.json()["eaten_at"] is None

    dish_check = await client.get(f"/nutrition/dishes/{dish_id}")
    assert dish_check.json()["remaining_g"] == 600

    # Now edit portion while planned: 200g -> 250g
    patch_resp = await client.patch(
        f"/nutrition/menu-items/{item_id}",
        json={"portion_g": 250},
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["portion_g"] == 250

    # 6. Mark eaten again -> 250g deducted (600 - 250 = 350g)
    await client.patch(
        f"/nutrition/menus/{menu_id}",
        json={"eaten_at": f"{test_date}T12:00:00Z"},
    )
    dish_check = await client.get(f"/nutrition/dishes/{dish_id}")
    assert dish_check.json()["remaining_g"] == 350

    # Deleting item while eaten is refused with 409
    del_refused = await client.delete(f"/nutrition/menu-items/{item_id}")
    assert del_refused.status_code == 409
    dish_check = await client.get(f"/nutrition/dishes/{dish_id}")
    assert dish_check.json()["remaining_g"] == 350

    # Unmark meal then delete
    await client.patch(f"/nutrition/menus/{menu_id}", json={"eaten_at": None})
    dish_check = await client.get(f"/nutrition/dishes/{dish_id}")
    assert dish_check.json()["remaining_g"] == 600

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
    await client.post("/nutrition/menus/2026-10-23/create")
    item_resp = await client.post(
        "/nutrition/menus/2026-10-23/items?meal_type=lunch",
        json={"dish_id": dish_a["id"], "portion_g": 200},
    )
    item_id = item_resp.json()["id"]
    menu_id = item_resp.json()["menu_id"]

    await client.patch(f"/nutrition/menus/{menu_id}", json={"eaten_at": "2026-10-23T12:00:00Z"})
    assert (await client.get(f"/nutrition/dishes/{dish_a['id']}")).json()["remaining_g"] == 300
    assert (await client.get(f"/nutrition/dishes/{dish_b['id']}")).json()["remaining_g"] == 500

    # Replacing Dish A with Dish B while eaten is refused with 409
    patch_refused = await client.patch(
        f"/nutrition/menu-items/{item_id}",
        json={"dish_id": dish_b["id"]},
    )
    assert patch_refused.status_code == 409
    assert (await client.get(f"/nutrition/dishes/{dish_a['id']}")).json()["remaining_g"] == 300
    assert (await client.get(f"/nutrition/dishes/{dish_b['id']}")).json()["remaining_g"] == 500

    # Unmark eaten meal first -> A restored to 500g
    unmark_resp = await client.patch(f"/nutrition/menus/{menu_id}", json={"eaten_at": None})
    assert unmark_resp.status_code == 200
    assert (await client.get(f"/nutrition/dishes/{dish_a['id']}")).json()["remaining_g"] == 500

    # Replace Dish A with Dish B (200g) on unmarked meal
    replace_resp = await client.patch(
        f"/nutrition/menu-items/{item_id}",
        json={"dish_id": dish_b["id"]},
    )
    assert replace_resp.status_code == 200

    # Mark eaten -> B deducted to 300g, A remains 500g
    await client.patch(f"/nutrition/menus/{menu_id}", json={"eaten_at": "2026-10-23T12:00:00Z"})
    assert (await client.get(f"/nutrition/dishes/{dish_a['id']}")).json()["remaining_g"] == 500
    assert (await client.get(f"/nutrition/dishes/{dish_b['id']}")).json()["remaining_g"] == 300


@pytest.mark.asyncio
async def test_meal_becomes_incomplete_when_dish_exhausted_or_deleted(client: AsyncClient):
    test_date = (dt_date.today() + timedelta(days=2)).isoformat()
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
                "cooked_on": test_date,
            },
        )
    ).json()
    p_id = potato["id"]

    await client.post(f"/nutrition/menus/{test_date}/create")

    # Form lunch (200g) and dinner (200g)
    lunch_item = (
        await client.post(
            f"/nutrition/menus/{test_date}/items?meal_type=lunch",
            json={"dish_id": p_id, "portion_g": 200},
        )
    ).json()
    await client.post(
        f"/nutrition/menus/{test_date}/items?meal_type=dinner",
        json={"dish_id": p_id, "portion_g": 200},
    )

    # Both lunch and dinner are planned
    menus = (await client.get(f"/nutrition/menus/{test_date}")).json()["menus"]
    assert all(m["status"] == "planned" for m in menus)

    # Eat lunch -> remaining becomes 300g
    await client.patch(
        f"/nutrition/menus/{lunch_item['menu_id']}",
        json={"eaten_at": f"{test_date}T12:00:00Z"},
    )
    assert (await client.get(f"/nutrition/dishes/{p_id}")).json()["remaining_g"] == 300

    # User manually corrects remaining potatoes to 0 (dropped on floor)
    await client.patch(f"/nutrition/dishes/{p_id}", json={"remaining_g": 0})

    # Verify:
    # 1. Lunch (eaten) remains eaten with 200g!
    # 2. Dinner (planned) now becomes incomplete!
    menus = (await client.get(f"/nutrition/menus/{test_date}")).json()["menus"]
    menu_by_type = {m["meal_type"]: m for m in menus}
    assert menu_by_type["lunch"]["status"] == "eaten"
    assert menu_by_type["dinner"]["status"] == "incomplete"

    # Deleting potato dish leaves Lunch eaten with title,
    # Dinner planned (free-text item without reference is not incomplete)
    await client.delete(f"/nutrition/dishes/{p_id}")
    menus = (await client.get(f"/nutrition/menus/{test_date}")).json()["menus"]
    menu_by_type = {m["meal_type"]: m for m in menus}
    assert menu_by_type["lunch"]["status"] == "eaten"
    assert menu_by_type["lunch"]["items"][0]["dish_id"] is None
    assert menu_by_type["lunch"]["items"][0]["title"] == "Картопля варена"
    assert menu_by_type["dinner"]["status"] == "planned"
    assert menu_by_type["dinner"]["items"][0]["incomplete"] is False


@pytest.mark.asyncio
async def test_menu_user_isolation(client_factory, make_user):
    user_a = await make_user("menu_a@example.com")
    user_b = await make_user("menu_b@example.com")

    client1 = client_factory(user_a)
    await client1.post("/nutrition/menus/2026-10-23/create")
    dish_resp = await client1.post(
        "/nutrition/dishes",
        json={
            "name": "Сир кисломолочний 5%",
            "kind": "ready",
            "cooked_weight_g": 500,
            "cooked_on": "2026-10-23",
        },
    )
    dish_id = dish_resp.json()["id"]

    item_resp = await client1.post(
        "/nutrition/menus/2026-10-23/items?meal_type=breakfast",
        json={
            "dish_id": dish_id,
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
        json={"eaten_at": "2026-10-23T12:00:00Z"},
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
async def test_meal_mark_unmark_with_stock(client: AsyncClient):
    test_date = (dt_date.today() + timedelta(days=2)).isoformat()
    await client.post(f"/nutrition/menus/{test_date}/create")
    dish = (
        await client.post(
            "/nutrition/dishes",
            json={
                "name": "Запечене філе індички",
                "kind": "ready",
                "cooked_weight_g": 500,
                "cooked_on": test_date,
            },
        )
    ).json()
    dish_id = dish["id"]

    item = (
        await client.post(
            f"/nutrition/menus/{test_date}/items?meal_type=lunch",
            json={"dish_id": dish_id, "portion_g": 200},
        )
    ).json()
    menu_id = item["menu_id"]

    # Initially unmarked, remaining_g is 500
    assert (await client.get(f"/nutrition/dishes/{dish_id}")).json()["remaining_g"] == 500

    # Mark meal eaten with eaten_at
    fixed_time = f"{test_date}T12:30:00Z"
    patch1 = (
        await client.patch(
            f"/nutrition/menus/{menu_id}",
            json={"eaten_at": fixed_time},
        )
    ).json()
    assert patch1["status"] == "eaten"
    assert patch1["eaten_at"] == fixed_time
    assert (await client.get(f"/nutrition/dishes/{dish_id}")).json()["remaining_g"] == 300

    # Meal is now eaten
    day_menus = (await client.get(f"/nutrition/menus/{test_date}")).json()["menus"]
    menu = next(m for m in day_menus if m["meal_type"] == "lunch")
    assert menu["status"] == "eaten"
    assert menu["eaten_at"] == fixed_time

    # Explicit null unmarks and clears eaten_at, restores stock
    patch2 = (
        await client.patch(
            f"/nutrition/menus/{menu_id}",
            json={"eaten_at": None},
        )
    ).json()
    assert patch2["status"] == "planned"
    assert patch2["eaten_at"] is None
    assert (await client.get(f"/nutrition/dishes/{dish_id}")).json()["remaining_g"] == 500

    # Meal is back to planned
    day_menus = (await client.get(f"/nutrition/menus/{test_date}")).json()["menus"]
    menu = next(m for m in day_menus if m["meal_type"] == "lunch")
    assert menu["status"] == "planned"
    assert menu["eaten_at"] is None

    # Omitted field is no-op
    patch3 = (
        await client.patch(
            f"/nutrition/menus/{menu_id}",
            json={},
        )
    ).json()
    assert patch3["status"] == "planned"
    assert patch3["eaten_at"] is None
    assert (await client.get(f"/nutrition/dishes/{dish_id}")).json()["remaining_g"] == 500


@pytest.mark.asyncio
async def test_bulk_meal_patch(client: AsyncClient):
    test_date = (dt_date.today() + timedelta(days=2)).isoformat()
    await client.post(f"/nutrition/menus/{test_date}/create")
    dish = (
        await client.post(
            "/nutrition/dishes",
            json={
                "name": "Плов з куркою",
                "kind": "ready",
                "cooked_weight_g": 600,
                "cooked_on": test_date,
            },
        )
    ).json()

    # Add 2 items to Lunch
    item1 = (
        await client.post(
            f"/nutrition/menus/{test_date}/items?meal_type=lunch",
            json={"dish_id": dish["id"], "portion_g": 250},
        )
    ).json()
    await client.post(
        f"/nutrition/menus/{test_date}/items?meal_type=lunch",
        json={"title": "Огірок свіжий", "portion_g": 100},
    )
    menu_id = item1["menu_id"]

    # Mark meal eaten
    meal_eaten = (
        await client.patch(
            f"/nutrition/menus/{menu_id}",
            json={"eaten_at": f"{test_date}T13:00:00Z"},
        )
    ).json()
    assert meal_eaten["status"] == "eaten"
    assert meal_eaten["eaten_at"] == f"{test_date}T13:00:00Z"
    assert (await client.get(f"/nutrition/dishes/{dish['id']}")).json()["remaining_g"] == 350

    # Mark meal planned (unmark meal)
    meal_planned = (
        await client.patch(
            f"/nutrition/menus/{menu_id}",
            json={"eaten_at": None},
        )
    ).json()
    assert meal_planned["status"] == "planned"
    assert meal_planned["eaten_at"] is None
    assert (await client.get(f"/nutrition/dishes/{dish['id']}")).json()["remaining_g"] == 600


@pytest.mark.asyncio
async def test_swap_dish_on_eaten_item(client: AsyncClient):
    test_date = (dt_date.today() + timedelta(days=2)).isoformat()
    await client.post(f"/nutrition/menus/{test_date}/create")
    dish_a = (
        await client.post(
            "/nutrition/dishes",
            json={
                "name": "Страва A",
                "kind": "ready",
                "cooked_weight_g": 500,
                "cooked_on": test_date,
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
                "cooked_on": test_date,
            },
        )
    ).json()

    # Add item with dish A, portion 150g, and mark meal eaten
    item = (
        await client.post(
            f"/nutrition/menus/{test_date}/items?meal_type=lunch",
            json={"dish_id": dish_a["id"], "portion_g": 150},
        )
    ).json()
    menu_id = item["menu_id"]
    await client.patch(f"/nutrition/menus/{menu_id}", json={"eaten_at": f"{test_date}T13:00:00Z"})

    assert (await client.get(f"/nutrition/dishes/{dish_a['id']}")).json()["remaining_g"] == 350
    assert (await client.get(f"/nutrition/dishes/{dish_b['id']}")).json()["remaining_g"] == 400

    # Swapping dish A for dish B on the eaten meal is refused with 409
    patch_swap = await client.patch(
        f"/nutrition/menu-items/{item['id']}",
        json={"dish_id": dish_b["id"], "portion_g": 200},
    )
    assert patch_swap.status_code == 409
    assert (await client.get(f"/nutrition/dishes/{dish_a['id']}")).json()["remaining_g"] == 350
    assert (await client.get(f"/nutrition/dishes/{dish_b['id']}")).json()["remaining_g"] == 400

    # Unmark meal first
    await client.patch(f"/nutrition/menus/{menu_id}", json={"eaten_at": None})
    assert (await client.get(f"/nutrition/dishes/{dish_a['id']}")).json()["remaining_g"] == 500

    # Swap dish A for dish B and change portion to 200g on unmarked meal
    patch_swap = await client.patch(
        f"/nutrition/menu-items/{item['id']}",
        json={"dish_id": dish_b["id"], "portion_g": 200},
    )
    assert patch_swap.status_code == 200
    assert patch_swap.json()["dish_id"] == dish_b["id"]
    assert patch_swap.json()["portion_g"] == 200

    # Remark meal as eaten -> Dish B deducted (-200g = 200g), Dish A remains restored (500g)
    await client.patch(f"/nutrition/menus/{menu_id}", json={"eaten_at": f"{test_date}T13:00:00Z"})
    assert (await client.get(f"/nutrition/dishes/{dish_a['id']}")).json()["remaining_g"] == 500
    assert (await client.get(f"/nutrition/dishes/{dish_b['id']}")).json()["remaining_g"] == 200


@pytest.mark.asyncio
async def test_unmarked_meal_on_previous_date_does_not_make_today_incomplete(
    client: AsyncClient,
):
    prev_date = (dt_date.today() - timedelta(days=1)).isoformat()
    today_date = (dt_date.today() + timedelta(days=2)).isoformat()

    await client.post(f"/nutrition/menus/{prev_date}/create")
    await client.post(f"/nutrition/menus/{today_date}/create")

    # Dish with 200g cooked
    dish = (
        await client.post(
            "/nutrition/dishes",
            json={
                "name": "Суп овочевий",
                "kind": "ready",
                "cooked_weight_g": 200,
                "cooked_on": prev_date,
            },
        )
    ).json()

    # Yesterday: Unmarked item with 200g
    await client.post(
        f"/nutrition/menus/{prev_date}/items?meal_type=lunch",
        json={"dish_id": dish["id"], "portion_g": 200},
    )

    # Today: Unmarked item with 200g
    await client.post(
        f"/nutrition/menus/{today_date}/items?meal_type=lunch",
        json={"dish_id": dish["id"], "portion_g": 200},
    )

    # Remaining in DB is still 200g because neither item is eaten
    assert (await client.get(f"/nutrition/dishes/{dish['id']}")).json()["remaining_g"] == 200

    # Today's menu must be PLANNED (not incomplete):
    # yesterday's unmarked item is not considered
    today_menu_resp = await client.get(f"/nutrition/menus/{today_date}")
    assert today_menu_resp.status_code == 200
    lunch = next(m for m in today_menu_resp.json()["menus"] if m["meal_type"] == "lunch")
    assert lunch["status"] == "planned"
    assert lunch["items"][0]["incomplete"] is False


@pytest.mark.asyncio
async def test_generation_skips_eaten_meals_and_replaces_planned_meals(client: AsyncClient):
    test_date = (dt_date.today() + timedelta(days=2)).isoformat()
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
                {"type": "breakfast", "time": "08:00"},
                {"type": "lunch", "time": "13:00"},
                {"type": "dinner", "time": "19:00"},
            ],
        },
    )
    await client.post(f"/nutrition/menus/{test_date}/create")
    dish = (
        await client.post(
            "/nutrition/dishes",
            json={
                "name": "Індичка запечена",
                "kind": "ready",
                "cooked_weight_g": 600,
                "cooked_on": test_date,
            },
        )
    ).json()

    # Breakfast: eaten
    b_item = (
        await client.post(
            f"/nutrition/menus/{test_date}/items?meal_type=breakfast",
            json={"dish_id": dish["id"], "portion_g": 100, "position": 1},
        )
    ).json()
    await client.patch(
        f"/nutrition/menus/{b_item['menu_id']}",
        json={"eaten_at": f"{test_date}T08:30:00Z"},
    )

    # Lunch: planned, with an old item
    old_lunch_item = (
        await client.post(
            f"/nutrition/menus/{test_date}/items?meal_type=lunch",
            json={"dish_id": dish["id"], "portion_g": 100, "position": 1},
        )
    ).json()

    mock_gemini = GeminiResult(
        data={
            "rationale": "Оновлення обіду",
            "shopping_list": [],
            "items": [
                {
                    "meal_type": "lunch",
                    "dish_id": dish["id"],
                    "title": "Індичка запечена",
                    "portion_g": 200,
                    "portion_text": "200 г",
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
            f"/nutrition/menus/{test_date}/generate",
            json={"scope": "day", "now": f"{test_date}T06:00:00Z"},
        )
        assert gen_resp.status_code == 200

    # Verify meals
    menu_resp = await client.get(f"/nutrition/menus/{test_date}")
    menus = {m["meal_type"]: m for m in menu_resp.json()["menus"]}

    # Breakfast was eaten -> untouched, original item kept
    assert menus["breakfast"]["status"] == "eaten"
    assert len(menus["breakfast"]["items"]) == 1
    assert menus["breakfast"]["items"][0]["id"] == b_item["id"]
    assert menus["breakfast"]["items"][0]["portion_g"] == 100

    # Lunch was planned -> all items replaced with new generated items
    assert menus["lunch"]["status"] == "planned"
    assert len(menus["lunch"]["items"]) == 1
    assert menus["lunch"]["items"][0]["id"] != old_lunch_item["id"]
    assert menus["lunch"]["items"][0]["portion_g"] == 200

    # Generating slot scope for an eaten meal returns 409
    slot_gen_resp = await client.post(
        f"/nutrition/menus/{test_date}/generate",
        json={"scope": "slot", "meal_type": "breakfast", "now": f"{test_date}T06:00:00Z"},
    )
    assert slot_gen_resp.status_code == 409


@pytest.mark.asyncio
async def test_generated_slot_outside_profile_rejected_422(client: AsyncClient):
    test_date = (dt_date.today() + timedelta(days=2)).isoformat()
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
                {"type": "breakfast", "time": "08:00"},
                {"type": "lunch", "time": "13:00"},
                {"type": "dinner", "time": "19:00"},
            ],
        },
    )
    await client.post(f"/nutrition/menus/{test_date}/create")
    dish = (
        await client.post(
            "/nutrition/dishes",
            json={
                "name": "Омлет",
                "kind": "ready",
                "cooked_weight_g": 300,
                "cooked_on": test_date,
            },
        )
    ).json()

    # Gemini returns a meal type not in profile schedule
    mock_gemini = GeminiResult(
        data={
            "rationale": "Некоректний слот",
            "shopping_list": [],
            "items": [
                {
                    "meal_type": "afternoon_snack",
                    "dish_id": dish["id"],
                    "title": "Омлет",
                    "portion_g": 150,
                    "portion_text": "150 г",
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
            f"/nutrition/menus/{test_date}/generate",
            json={"scope": "day", "now": f"{test_date}T06:00:00Z"},
        )
        assert gen_resp.status_code == 422
        assert "not allowed in profile schedule" in gen_resp.json()["detail"]


@pytest.mark.asyncio
async def test_create_free_text_menu_item(client: AsyncClient):
    await client.post("/nutrition/menus/2026-10-27/create")
    # Free-text item with title, portions, and kcal
    resp1 = await client.post(
        "/nutrition/menus/2026-10-27/items?meal_type=lunch",
        json={
            "title": "  Кава з молоком  ",
            "portion_g": 200,
            "portion_text": "1 горнятко",
            "kcal": 85,
        },
    )
    assert resp1.status_code == 201
    item1 = resp1.json()
    assert item1["title"] == "Кава з молоком"
    assert item1["dish_id"] is None
    assert item1["portion_g"] == 200
    assert item1["portion_text"] == "1 горнятко"
    assert item1["kcal"] == 85
    assert item1["nutrition_source"] == "user"
    assert item1["incomplete"] is False
    assert item1["origin"] == "manual"
    assert "updated_at" in item1

    # Minimal free-text item (optional fields omitted)
    resp2 = await client.post(
        "/nutrition/menus/2026-10-27/items?meal_type=lunch",
        json={"title": "Зелений чай"},
    )
    assert resp2.status_code == 201
    item2 = resp2.json()
    assert item2["title"] == "Зелений чай"
    assert item2["portion_g"] is None
    assert item2["portion_text"] is None
    assert item2["kcal"] is None
    assert item2["nutrition_source"] is None
    assert item2["incomplete"] is False


@pytest.mark.asyncio
async def test_reject_empty_title_without_references(client: AsyncClient):
    await client.post("/nutrition/menus/2026-10-27/create")
    # Empty title without references
    resp_empty = await client.post(
        "/nutrition/menus/2026-10-27/items?meal_type=lunch",
        json={"title": ""},
    )
    assert resp_empty.status_code == 422

    # Whitespace-only title without references
    resp_ws = await client.post(
        "/nutrition/menus/2026-10-27/items?meal_type=lunch",
        json={"title": "   "},
    )
    assert resp_ws.status_code == 422

    # Omitted title without references
    resp_none = await client.post(
        "/nutrition/menus/2026-10-27/items?meal_type=lunch",
        json={},
    )
    assert resp_none.status_code == 422

    # Direct model validation
    with pytest.raises(ValueError):
        MenuItemCreate(title="")
    with pytest.raises(ValueError):
        MenuItemCreate(title="   ")
    with pytest.raises(ValueError):
        MenuItemCreate()


@pytest.mark.asyncio
async def test_reject_nonexistent_dish(client: AsyncClient):
    import uuid

    await client.post("/nutrition/menus/2026-10-27/create")
    resp = await client.post(
        "/nutrition/menus/2026-10-27/items?meal_type=lunch",
        json={
            "dish_id": str(uuid.uuid4()),
        },
    )
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_kcal_source_values(client: AsyncClient):
    await client.post("/nutrition/menus/2026-10-27/create")
    # 1. Manual item creation with kcal -> user
    resp = await client.post(
        "/nutrition/menus/2026-10-27/items?meal_type=breakfast",
        json={"title": "Яєчня", "portion_g": 150, "kcal": 220},
    )
    item = resp.json()
    item_id = item["id"]
    assert item["nutrition_source"] == "user"
    assert item["kcal"] == 220

    # 2. Update kcal to another value -> stays user
    patch_resp1 = await client.patch(
        f"/nutrition/menu-items/{item_id}",
        json={"kcal": 260},
    )
    assert patch_resp1.json()["nutrition_source"] == "user"
    assert patch_resp1.json()["kcal"] == 260

    # 3. Update kcal to None -> becomes None
    patch_resp2 = await client.patch(
        f"/nutrition/menu-items/{item_id}",
        json={"kcal": None},
    )
    assert patch_resp2.json()["nutrition_source"] is None
    assert patch_resp2.json()["kcal"] is None

    # 4. Update unrelated field (portion_g) -> nutrition_source stays None
    patch_resp3 = await client.patch(
        f"/nutrition/menu-items/{item_id}",
        json={"portion_g": 180},
    )
    assert patch_resp3.json()["nutrition_source"] is None
    assert patch_resp3.json()["portion_g"] == 180

    # 5. Set kcal again -> becomes user
    patch_resp4 = await client.patch(
        f"/nutrition/menu-items/{item_id}",
        json={"kcal": 250},
    )
    assert patch_resp4.json()["nutrition_source"] == "user"
    assert patch_resp4.json()["kcal"] == 250

    # 6. Estimate free-text item -> model
    await client.patch(f"/nutrition/menu-items/{item_id}", json={"kcal": None})
    mock_gemini = GeminiResult(
        data={
            "items": [
                {
                    "id": item_id,
                    "kcal": 240.0,
                    "protein_g": 14.0,
                    "fat_g": 18.0,
                    "carbs_g": 2.0,
                    "fiber_g": None,
                    "sugar_g": None,
                    "sodium_mg": 200.0,
                }
            ]
        },
        raw_text="{}",
        model="gemini-2.0-flash",
        input_tokens=100,
        output_tokens=50,
    )
    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_gen:
        mock_gen.return_value = mock_gemini
        est_resp = await client.post("/nutrition/menus/2026-10-27/estimate")
        assert est_resp.status_code == 200

    menus = (await client.get("/nutrition/menus/2026-10-27")).json()["menus"]
    est_item = next(it for m in menus for it in m["items"] if it["id"] == item_id)
    assert est_item["nutrition_source"] == "model"
    assert est_item["kcal"] == 240.0

    # 7. Dish item -> computed
    prod_resp = await client.post(
        "/nutrition/products",
        json={
            "name": "Горох",
            "stock_g": 500,
            "kcal": 100,
            "protein_g": 8,
            "fat_g": 1,
            "carbs_g": 15,
        },
    )
    dish = (
        await client.post(
            "/nutrition/dishes",
            json={
                "name": "Суп гороховий",
                "kind": "ready",
                "cooked_weight_g": 400,
                "cooked_on": "2026-10-27",
                "components": [{"product_id": prod_resp.json()["id"], "weight_g": 400}],
            },
        )
    ).json()

    dish_item_resp = await client.post(
        "/nutrition/menus/2026-10-27/items?meal_type=lunch",
        json={"dish_id": dish["id"], "portion_g": 200},
    )
    assert dish_item_resp.json()["nutrition_source"] == "computed"
    assert dish_item_resp.json()["kcal"] == 200.0


@pytest.mark.asyncio
async def test_incomplete_flag_for_free_text_item(client: AsyncClient):
    test_date = (dt_date.today() + timedelta(days=2)).isoformat()
    await client.post(f"/nutrition/menus/{test_date}/create")
    # Free-text item in Dinner
    item_resp = await client.post(
        f"/nutrition/menus/{test_date}/items?meal_type=dinner",
        json={"title": "Салат овочевий", "portion_g": 200, "kcal": 100},
    )
    assert item_resp.status_code == 201
    item = item_resp.json()
    assert item["incomplete"] is False

    # Whole menu should be planned
    menus = (await client.get(f"/nutrition/menus/{test_date}")).json()["menus"]
    dinner = [m for m in menus if m["meal_type"] == "dinner"][0]
    assert dinner["status"] == "planned"
    assert dinner["items"][0]["incomplete"] is False


@pytest.mark.asyncio
async def test_canvas_projection_of_profile_meals(client: AsyncClient, session: AsyncSession):
    future_date = (dt_date.today() + timedelta(days=5)).isoformat()
    future_d = dt_date.today() + timedelta(days=5)
    # Setup profile with 3 meals
    await client.put(
        "/nutrition/profile",
        json={
            "sex": "male",
            "birth_year": 1980,
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

    # 1. GET returns canvas instead of 404
    resp = await client.get(f"/nutrition/menus/{future_date}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["day_created"] is False
    assert len(data["menus"]) == 3

    # All projected meals have id=null, status=planned, items=[]
    for m in data["menus"]:
        assert m["id"] is None
        assert m["status"] == "planned"
        assert m["items"] == []
        assert m["regenerations"] == 0
        assert m["time"] in ("08:00", "13:00", "19:00")

    # 2. Nothing is written to DB on GET
    rows = (await session.exec(select(Menu).where(Menu.date == future_d))).all()
    assert len(rows) == 0


@pytest.mark.asyncio
async def test_canvas_delete_projected_and_persisted_meals(client: AsyncClient):
    await client.put(
        "/nutrition/profile",
        json={
            "sex": "male",
            "birth_year": 1985,
            "height_cm": 175,
            "weight_kg": 75.0,
            "target_weight_kg": 70.0,
            "pace_kg_per_week": 0.5,
            "meals": [
                {"type": "breakfast", "time": "08:00"},
                {"type": "lunch", "time": "13:00"},
                {"type": "dinner", "time": "19:00"},
            ],
        },
    )

    # Setup ready dish
    dish = (
        await client.post(
            "/nutrition/dishes",
            json={
                "name": "Плов",
                "kind": "ready",
                "cooked_weight_g": 600,
                "cooked_on": "2026-10-28",
            },
        )
    ).json()

    # 1. Delete projected meal (Dinner)
    del_dinner = await client.delete("/nutrition/menus/2026-10-28/meals/dinner")
    assert del_dinner.status_code == 204

    # Second delete returns 404
    del_dinner_again = await client.delete("/nutrition/menus/2026-10-28/meals/dinner")
    assert del_dinner_again.status_code == 404

    # Dinner is not projected again
    day_resp = await client.get("/nutrition/menus/2026-10-28")
    assert day_resp.status_code == 200
    types = [m["meal_type"] for m in day_resp.json()["menus"]]
    assert types == ["breakfast", "lunch"]

    # 2. Persisted meal with eaten mark (Lunch)
    item_resp = await client.post(
        "/nutrition/menus/2026-10-28/items?meal_type=lunch",
        json={"dish_id": dish["id"], "portion_g": 200},
    )
    assert item_resp.status_code == 201
    menu_id = item_resp.json()["menu_id"]
    await client.patch(
        f"/nutrition/menus/{menu_id}",
        json={"eaten_at": "2026-10-28T12:00:00Z"},
    )

    # Dish stock was deducted
    dish_state = (await client.get(f"/nutrition/dishes/{dish['id']}")).json()
    assert dish_state["remaining_g"] == 400

    # Delete persisted Lunch while eaten is refused with 409
    del_lunch = await client.delete("/nutrition/menus/2026-10-28/meals/lunch")
    assert del_lunch.status_code == 409

    # Stock is not restored, meal still on canvas
    dish_state2 = (await client.get(f"/nutrition/dishes/{dish['id']}")).json()
    assert dish_state2["remaining_g"] == 400

    # Unmark eaten meal (eaten_at back to null)
    await client.patch(f"/nutrition/menus/{menu_id}", json={"eaten_at": None})
    dish_state3 = (await client.get(f"/nutrition/dishes/{dish['id']}")).json()
    assert dish_state3["remaining_g"] == 600

    # Now delete persisted Lunch without eaten mark -> 204
    del_lunch_ok = await client.delete("/nutrition/menus/2026-10-28/meals/lunch")
    assert del_lunch_ok.status_code == 204

    # Second delete returns 404
    del_lunch_again = await client.delete("/nutrition/menus/2026-10-28/meals/lunch")
    assert del_lunch_again.status_code == 404

    # Canvas now only has Breakfast
    day_resp2 = await client.get("/nutrition/menus/2026-10-28")
    assert len(day_resp2.json()["menus"]) == 1
    assert day_resp2.json()["menus"][0]["meal_type"] == "breakfast"

    # 3. Delete non-existent meal type -> 404
    del_nonexistent = await client.delete("/nutrition/menus/2026-10-28/meals/evening_snack")
    assert del_nonexistent.status_code == 404


@pytest.mark.asyncio
async def test_canvas_add_new_revive_and_conflict(client: AsyncClient):
    test_date = (dt_date.today() + timedelta(days=2)).isoformat()
    await client.put(
        "/nutrition/profile",
        json={
            "sex": "male",
            "birth_year": 1990,
            "height_cm": 182,
            "weight_kg": 82.0,
            "target_weight_kg": 78.0,
            "pace_kg_per_week": 0.5,
            "meals": [
                {"type": "breakfast", "time": "08:00"},
            ],
        },
    )
    await client.post(f"/nutrition/menus/{test_date}/create")

    # 1. 409 when adding active meal that already exists on canvas (persisted from profile)
    resp_conflict = await client.post(
        f"/nutrition/menus/{test_date}/meals",
        json={"meal_type": "breakfast", "time": "08:30"},
    )
    assert resp_conflict.status_code == 409

    # 2. Add brand new custom meal -> 201
    resp_create = await client.post(
        f"/nutrition/menus/{test_date}/meals",
        json={"meal_type": "afternoon_snack", "time": "16:00"},
    )
    assert resp_create.status_code == 201
    meal = resp_create.json()
    assert meal["id"] is not None
    assert meal["meal_type"] == "afternoon_snack"
    assert meal["time"] == "16:00"
    assert meal["status"] == "planned"

    # Adding it again while active -> 409
    resp_conflict2 = await client.post(
        f"/nutrition/menus/{test_date}/meals",
        json={"meal_type": "afternoon_snack", "time": "16:30"},
    )
    assert resp_conflict2.status_code == 409

    # 3. Delete persisted Breakfast -> 204 (tombstone created)
    del_resp = await client.delete(f"/nutrition/menus/{test_date}/meals/breakfast")
    assert del_resp.status_code == 204

    # 4. Revive tombstone -> 201
    resp_revive = await client.post(
        f"/nutrition/menus/{test_date}/meals",
        json={"meal_type": "breakfast", "time": "09:00"},
    )
    assert resp_revive.status_code == 201
    revived = resp_revive.json()
    assert revived["id"] is not None
    assert revived["meal_type"] == "breakfast"
    assert revived["time"] == "09:00"

    # Both breakfast and afternoon_snack are active on canvas
    day_resp = await client.get(f"/nutrition/menus/{test_date}")
    assert len(day_resp.json()["menus"]) == 2


@pytest.mark.asyncio
async def test_canvas_add_item_to_removed_slot_conflict(client: AsyncClient):
    test_date = (dt_date.today() + timedelta(days=2)).isoformat()
    await client.put(
        "/nutrition/profile",
        json={
            "sex": "female",
            "birth_year": 1992,
            "height_cm": 165,
            "weight_kg": 60.0,
            "target_weight_kg": 55.0,
            "pace_kg_per_week": 0.5,
            "meals": [
                {"type": "lunch", "time": "13:00"},
            ],
        },
    )
    await client.post(f"/nutrition/menus/{test_date}/create")

    # Delete Lunch -> tombstone
    del_resp = await client.delete(f"/nutrition/menus/{test_date}/meals/lunch")
    assert del_resp.status_code == 204

    # Try to add item to removed slot -> 409 with clear message
    add_resp = await client.post(
        f"/nutrition/menus/{test_date}/items?meal_type=lunch",
        json={"title": "Борщ", "portion_g": 300, "kcal": 200},
    )
    assert add_resp.status_code == 409
    assert "removed" in add_resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_canvas_implicit_creation_copies_time(client: AsyncClient, session: AsyncSession):
    test_date = (dt_date.today() + timedelta(days=2)).isoformat()
    test_d = dt_date.today() + timedelta(days=2)
    await client.put(
        "/nutrition/profile",
        json={
            "sex": "male",
            "birth_year": 1985,
            "height_cm": 180,
            "weight_kg": 75.0,
            "target_weight_kg": 70.0,
            "pace_kg_per_week": 0.5,
            "meals": [
                {"type": "breakfast", "time": "08:15"},
            ],
        },
    )
    await client.post(f"/nutrition/menus/{test_date}/create")

    # Add item to created day meal
    add_resp = await client.post(
        f"/nutrition/menus/{test_date}/items?meal_type=breakfast",
        json={"title": "Омлет", "portion_g": 150, "kcal": 250},
    )
    assert add_resp.status_code == 201

    row = (
        await session.exec(
            select(Menu).where(
                Menu.date == test_d,
                Menu.meal_type == MealType.BREAKFAST,
            )
        )
    ).first()
    assert row is not None
    assert row.time == "08:15"


@pytest.mark.asyncio
async def test_canvas_ordering_by_time_with_nulls_last(client: AsyncClient):
    test_date = (dt_date.today() + timedelta(days=2)).isoformat()
    # Setup profile with unordered meals
    await client.put(
        "/nutrition/profile",
        json={
            "sex": "male",
            "birth_year": 1980,
            "height_cm": 180,
            "weight_kg": 80.0,
            "target_weight_kg": 75.0,
            "pace_kg_per_week": 0.5,
            "meals": [
                {"type": "dinner", "time": "20:00"},
                {"type": "breakfast", "time": "08:00"},
                {"type": "lunch", "time": "13:00"},
            ],
        },
    )
    await client.post(f"/nutrition/menus/{test_date}/create")

    # Add custom meal with time
    resp1 = await client.post(
        f"/nutrition/menus/{test_date}/meals",
        json={"meal_type": "afternoon_snack", "time": "16:00"},
    )
    assert resp1.status_code == 201

    # Add items to create meals without time
    # (types not in profile schedule)
    await client.post(
        f"/nutrition/menus/{test_date}/items?meal_type=evening_snack",
        json={"title": "Кефір", "portion_g": 200},
    )
    await client.post(
        f"/nutrition/menus/{test_date}/items?meal_type=morning_snack",
        json={"title": "Вода", "portion_g": 200},
    )

    day_resp = await client.get(f"/nutrition/menus/{test_date}")
    assert day_resp.status_code == 200
    menus = day_resp.json()["menus"]

    # Verify slots order:
    # 08:00 (breakfast), 13:00 (lunch), 16:00 (afternoon_snack), 20:00 (dinner),
    # no time alphabetical: evening_snack, morning_snack
    types = [m["meal_type"] for m in menus]
    assert types == [
        "breakfast",
        "lunch",
        "afternoon_snack",
        "dinner",
        "evening_snack",
        "morning_snack",
    ]


@pytest.mark.asyncio
async def test_canvas_generation_does_not_touch_removed_meals(client: AsyncClient):
    test_date = (dt_date.today() + timedelta(days=2)).isoformat()
    await client.put(
        "/nutrition/profile",
        json={
            "sex": "male",
            "birth_year": 1980,
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
    await client.post(f"/nutrition/menus/{test_date}/create")
    dish = (
        await client.post(
            "/nutrition/dishes",
            json={
                "name": "Рагу",
                "kind": "ready",
                "cooked_weight_g": 800,
                "cooked_on": test_date,
            },
        )
    ).json()

    # Remove Lunch
    del_resp = await client.delete(f"/nutrition/menus/{test_date}/meals/lunch")
    assert del_resp.status_code == 204

    # 1. Slot scope generation for removed slot -> 400
    gen_slot_resp = await client.post(
        f"/nutrition/menus/{test_date}/generate",
        json={"scope": "slot", "meal_type": "lunch", "now": f"{test_date}T06:00:00Z"},
    )
    assert gen_slot_resp.status_code == 400

    # 2. Day scope generation ignores removed slot and never revives it
    mock_gemini = GeminiResult(
        data={
            "rationale": "Тест генерації без видаленого обіду",
            "shopping_list": [],
            "items": [
                {
                    "meal_type": "breakfast",
                    "dish_id": dish["id"],
                    "title": "Рагу",
                    "portion_g": 250,
                    "portion_text": "250 г",
                    "position": 1,
                },
                {
                    "meal_type": "dinner",
                    "dish_id": dish["id"],
                    "title": "Рагу",
                    "portion_g": 200,
                    "portion_text": "200 г",
                    "position": 1,
                },
            ],
        },
        raw_text="{}",
        model="gemini-2.0-flash",
        input_tokens=100,
        output_tokens=50,
    )
    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_gen:
        mock_gen.return_value = mock_gemini
        gen_resp = await client.post(
            f"/nutrition/menus/{test_date}/generate",
            json={"scope": "day", "now": f"{test_date}T06:00:00Z"},
        )
        assert gen_resp.status_code == 200
        gen_menus = gen_resp.json()["menus"]
        assert [m["meal_type"] for m in gen_menus] == ["breakfast", "dinner"]

    day_menus = (await client.get(f"/nutrition/menus/{test_date}")).json()["menus"]
    assert [m["meal_type"] for m in day_menus] == ["breakfast", "dinner"]


@pytest.mark.asyncio
async def test_menu_update_eaten_at_validation(client: AsyncClient):
    test_date = (dt_date.today() + timedelta(days=2)).isoformat()
    await client.post(f"/nutrition/menus/{test_date}/create")
    # Create a meal with item
    item_resp = await client.post(
        f"/nutrition/menus/{test_date}/items?meal_type=breakfast",
        json={"title": "Каша", "portion_g": 200},
    )
    assert item_resp.status_code == 201
    menu_id = item_resp.json()["menu_id"]

    # Invalid datetime rejected with 422
    patch_invalid = await client.patch(
        f"/nutrition/menus/{menu_id}",
        json={"eaten_at": "not-a-datetime"},
    )
    assert patch_invalid.status_code == 422

    # Datetime marks meal eaten
    fixed_time = f"{test_date}T08:30:00Z"
    patch_eaten = await client.patch(
        f"/nutrition/menus/{menu_id}",
        json={"eaten_at": fixed_time},
    )
    assert patch_eaten.status_code == 200
    assert patch_eaten.json()["status"] == "eaten"
    assert patch_eaten.json()["eaten_at"] == fixed_time

    # Explicit null unmarks meal (back to planned)
    patch_planned = await client.patch(
        f"/nutrition/menus/{menu_id}",
        json={"eaten_at": None},
    )
    assert patch_planned.status_code == 200
    assert patch_planned.json()["status"] == "planned"
    assert patch_planned.json()["eaten_at"] is None


@pytest.mark.asyncio
async def test_alembic_migration_backfill_query(client: AsyncClient, session: AsyncSession):
    # Create profile via client
    await client.put(
        "/nutrition/profile",
        json={
            "sex": "male",
            "birth_year": 1980,
            "height_cm": 180,
            "weight_kg": 80.0,
            "target_weight_kg": 75.0,
            "pace_kg_per_week": 0.5,
            "meals": [
                {"type": "breakfast", "time": "08:45"},
                {"type": "lunch", "time": "13:30"},
            ],
        },
    )

    user = (await session.exec(select(User).limit(1))).first()
    assert user is not None

    # Insert menus with time=None
    m1 = Menu(
        user_id=user.id,
        date=dt_date(2026, 10, 20),
        meal_type=MealType.BREAKFAST,
        time=None,
    )
    m2 = Menu(
        user_id=user.id,
        date=dt_date(2026, 10, 20),
        meal_type=MealType.LUNCH,
        time=None,
    )
    m3 = Menu(
        user_id=user.id,
        date=dt_date(2026, 10, 20),
        meal_type=MealType.DINNER,
        time=None,
    )
    session.add(m1)
    session.add(m2)
    session.add(m3)
    await session.commit()

    # Execute migration SQL
    await session.exec(
        text(
            """
            UPDATE menus m
            SET time = sub.meal_time
            FROM (
                SELECT p.user_id,
                       elem->>'type' AS meal_type,
                       elem->>'time' AS meal_time
                FROM nutrition_profiles p,
                     jsonb_array_elements(p.meals) AS elem
            ) sub
            WHERE m.user_id = sub.user_id
              AND m.meal_type::text = sub.meal_type
              AND m.time IS NULL;
            """
        )
    )
    await session.commit()

    await session.refresh(m1)
    await session.refresh(m2)
    await session.refresh(m3)

    assert m1.time == "08:45"
    assert m2.time == "13:30"
    assert m3.time is None


@pytest.mark.asyncio
async def test_delete_meal_with_eaten_item_refused(client: AsyncClient):
    date_str = (dt_date.today() + timedelta(days=2)).isoformat()
    await client.put(
        "/nutrition/profile",
        json={
            "sex": "male",
            "birth_year": 1990,
            "height_cm": 180,
            "weight_kg": 80.0,
            "target_weight_kg": 75.0,
            "pace_kg_per_week": 0.5,
            "meals": [
                {"type": "breakfast", "time": "08:00"},
                {"type": "lunch", "time": "13:00"},
            ],
        },
    )
    await client.post(f"/nutrition/menus/{date_str}/create")

    # 1. Normal meal with eaten item
    dish = (
        await client.post(
            "/nutrition/dishes",
            json={
                "name": "Суп курячий",
                "kind": "ready",
                "cooked_weight_g": 500,
                "cooked_on": date_str,
            },
        )
    ).json()

    item_resp = await client.post(
        f"/nutrition/menus/{date_str}/items?meal_type=lunch",
        json={"dish_id": dish["id"], "portion_g": 200},
    )
    assert item_resp.status_code == 201
    menu_id = item_resp.json()["menu_id"]
    await client.patch(
        f"/nutrition/menus/{menu_id}",
        json={"eaten_at": f"{date_str}T13:00:00Z"},
    )

    # Dish stock deducted: 500 - 200 = 300
    assert (await client.get(f"/nutrition/dishes/{dish['id']}")).json()["remaining_g"] == 300

    # DELETE /menus/{date}/meals/lunch fails with 409
    del_res = await client.delete(f"/nutrition/menus/{date_str}/meals/lunch")
    assert del_res.status_code == 409
    assert "eaten" in del_res.json()["detail"].lower()

    # Nothing changed: stock still 300, meal still present and eaten
    assert (await client.get(f"/nutrition/dishes/{dish['id']}")).json()["remaining_g"] == 300
    day = (await client.get(f"/nutrition/menus/{date_str}")).json()
    lunch = next(m for m in day["menus"] if m["meal_type"] == "lunch")
    assert lunch["status"] == "eaten"
    assert len(lunch["items"]) == 1

    # 2. Pinned meal with eaten mark
    await client.put(
        "/nutrition/pinned-meals/breakfast",
        json={
            "active": True,
            "items": [
                {"title": "Вівсянка", "portion_g": 150, "kcal": 200, "position": 1},
            ],
        },
    )

    # Materialize on canvas
    day_pinned = (await client.get(f"/nutrition/menus/{date_str}")).json()
    breakfast = next(m for m in day_pinned["menus"] if m["meal_type"] == "breakfast")
    assert breakfast["pinned"] is True
    b_menu_id = breakfast["id"]

    # Mark as eaten
    mark_res = await client.patch(
        f"/nutrition/menus/{b_menu_id}",
        json={"eaten_at": f"{date_str}T08:30:00Z"},
    )
    assert mark_res.status_code == 200

    # DELETE /menus/{date}/meals/breakfast fails with 409
    del_pin_res = await client.delete(f"/nutrition/menus/{date_str}/meals/breakfast")
    assert del_pin_res.status_code == 409
    assert "eaten" in del_pin_res.json()["detail"].lower()

    # Nothing changed: still pinned, pin not dismissed, not in available_pins, meal still eaten
    day_pinned_after = (await client.get(f"/nutrition/menus/{date_str}")).json()
    b_after = next(m for m in day_pinned_after["menus"] if m["meal_type"] == "breakfast")
    assert b_after["pinned"] is True
    assert b_after["status"] == "eaten"
    assert len(b_after["items"]) == 1
    assert not any(p["meal_type"] == "breakfast" for p in day_pinned_after["available_pins"])


@pytest.mark.asyncio
async def test_patch_content_of_eaten_item_refused(client: AsyncClient):
    date_str = (dt_date.today() + timedelta(days=2)).isoformat()
    await client.post(f"/nutrition/menus/{date_str}/create")
    prod = (
        await client.post(
            "/nutrition/products",
            json={
                "name": "Яблуко",
                "stock_g": 1000,
                "kcal": 125.0,
                "protein_g": 5.0,
                "fat_g": 1.0,
                "carbs_g": 25.0,
            },
        )
    ).json()
    dish1 = (
        await client.post(
            "/nutrition/dishes",
            json={
                "name": "Рис з овочами",
                "kind": "ready",
                "cooked_weight_g": 400,
                "cooked_on": date_str,
                "components": [{"product_id": prod["id"], "weight_g": 400}],
            },
        )
    ).json()
    dish2 = (
        await client.post(
            "/nutrition/dishes",
            json={
                "name": "Гречка",
                "kind": "ready",
                "cooked_weight_g": 400,
                "cooked_on": date_str,
                "components": [{"product_id": prod["id"], "weight_g": 400}],
            },
        )
    ).json()

    item = (
        await client.post(
            f"/nutrition/menus/{date_str}/items?meal_type=dinner",
            json={
                "dish_id": dish1["id"],
                "title": "Рис з овочами",
                "portion_g": 200,
                "portion_text": "200 г",
            },
        )
    ).json()
    item_id = item["id"]
    menu_id = item["menu_id"]

    # Mark meal eaten
    await client.patch(
        f"/nutrition/menus/{menu_id}",
        json={"eaten_at": f"{date_str}T19:00:00Z"},
    )

    # Changing any content field on item of eaten meal -> 409
    content_patches = [
        {"dish_id": dish2["id"]},
        {"title": "Нова назва"},
        {"portion_g": 300},
        {"portion_text": "300 г"},
        {"kcal": 350.0},
    ]
    for payload in content_patches:
        res = await client.patch(f"/nutrition/menu-items/{item_id}", json=payload)
        assert res.status_code == 409, f"Payload {payload} should have been refused with 409"
        assert "eaten" in res.json()["detail"].lower()

    # Adding an item to an eaten meal -> 409
    add_res = await client.post(
        f"/nutrition/menus/{date_str}/items?meal_type=dinner",
        json={"title": "Ще страва", "portion_g": 100},
    )
    assert add_res.status_code == 409
    assert "eaten" in add_res.json()["detail"].lower()

    # Item content remained completely unchanged
    day = (await client.get(f"/nutrition/menus/{date_str}")).json()
    dinner = next(m for m in day["menus"] if m["meal_type"] == "dinner")
    assert dinner["status"] == "eaten"
    it = dinner["items"][0]
    assert it["dish_id"] == dish1["id"]
    assert it["title"] == "Рис з овочами"
    assert it["portion_g"] == 200
    assert it["portion_text"] == "200 г"
    assert it["kcal"] == 250.0


@pytest.mark.asyncio
async def test_eaten_item_unchanged_resend_accepted(client: AsyncClient):
    date_str = (dt_date.today() + timedelta(days=2)).isoformat()
    await client.post(f"/nutrition/menus/{date_str}/create")
    item = (
        await client.post(
            f"/nutrition/menus/{date_str}/items?meal_type=morning_snack",
            json={
                "title": "Горіхи",
                "portion_g": 50,
                "portion_text": "50 г",
                "kcal": 300,
            },
        )
    ).json()
    menu_id = item["menu_id"]

    # Mark meal eaten
    first_time = f"{date_str}T10:00:00Z"
    mark_res = await client.patch(
        f"/nutrition/menus/{menu_id}",
        json={"eaten_at": first_time},
    )
    assert mark_res.status_code == 200
    assert mark_res.json()["status"] == "eaten"
    assert mark_res.json()["eaten_at"] == first_time

    # Updating meal's eaten_at timestamp -> accepted 200
    new_time = f"{date_str}T10:30:00Z"
    resend = await client.patch(
        f"/nutrition/menus/{menu_id}",
        json={"eaten_at": new_time},
    )
    assert resend.status_code == 200
    assert resend.json()["status"] == "eaten"
    assert resend.json()["eaten_at"] == new_time


@pytest.mark.asyncio
async def test_eaten_item_unmark_then_edit_works(client: AsyncClient):
    date_str = (dt_date.today() + timedelta(days=2)).isoformat()
    await client.post(f"/nutrition/menus/{date_str}/create")
    item = (
        await client.post(
            f"/nutrition/menus/{date_str}/items?meal_type=lunch",
            json={
                "title": "Борщ",
                "portion_g": 300,
                "kcal": 220,
            },
        )
    ).json()
    item_id = item["id"]
    menu_id = item["menu_id"]

    # Mark meal eaten
    await client.patch(
        f"/nutrition/menus/{menu_id}",
        json={"eaten_at": f"{date_str}T13:00:00Z"},
    )

    # 1. While eaten, editing item is refused with 409
    patch_refused = await client.patch(
        f"/nutrition/menu-items/{item_id}",
        json={"portion_g": 350},
    )
    assert patch_refused.status_code == 409

    # 2. Unmark eaten mark (eaten_at back to null)
    unmark_res = await client.patch(
        f"/nutrition/menus/{menu_id}",
        json={"eaten_at": None},
    )
    assert unmark_res.status_code == 200
    assert unmark_res.json()["status"] == "planned"
    assert unmark_res.json()["eaten_at"] is None

    # 3. After unmarking, content can be edited as usual
    edit_res = await client.patch(
        f"/nutrition/menu-items/{item_id}",
        json={
            "title": "Борщ зі сметаною",
            "portion_g": 350,
            "kcal": 270,
        },
    )
    assert edit_res.status_code == 200
    assert edit_res.json()["title"] == "Борщ зі сметаною"
    assert edit_res.json()["portion_g"] == 350
    assert edit_res.json()["kcal"] == 270


@pytest.mark.asyncio
async def test_delete_eaten_item_refused(client: AsyncClient):
    date_str = (dt_date.today() + timedelta(days=2)).isoformat()
    await client.post(f"/nutrition/menus/{date_str}/create")
    dish = (
        await client.post(
            "/nutrition/dishes",
            json={
                "name": "Сирники",
                "kind": "ready",
                "cooked_weight_g": 300,
                "cooked_on": date_str,
            },
        )
    ).json()

    item = (
        await client.post(
            f"/nutrition/menus/{date_str}/items?meal_type=breakfast",
            json={"dish_id": dish["id"], "portion_g": 150},
        )
    ).json()
    item_id = item["id"]
    menu_id = item["menu_id"]

    # Mark meal eaten
    await client.patch(
        f"/nutrition/menus/{menu_id}",
        json={"eaten_at": f"{date_str}T08:00:00Z"},
    )

    # Dish stock deducted: 300 - 150 = 150
    assert (await client.get(f"/nutrition/dishes/{dish['id']}")).json()["remaining_g"] == 150

    # DELETE /menu-items/{id} on eaten meal -> 409
    del_res = await client.delete(f"/nutrition/menu-items/{item_id}")
    assert del_res.status_code == 409
    assert "eaten" in del_res.json()["detail"].lower()

    # Item is NOT deleted, dish stock NOT restored
    assert (await client.get(f"/nutrition/dishes/{dish['id']}")).json()["remaining_g"] == 150
    day = (await client.get(f"/nutrition/menus/{date_str}")).json()
    assert len(day["menus"][0]["items"]) == 1

    # Unmark meal -> stock restored
    unmark_res = await client.patch(
        f"/nutrition/menus/{menu_id}",
        json={"eaten_at": None},
    )
    assert unmark_res.status_code == 200
    assert (await client.get(f"/nutrition/dishes/{dish['id']}")).json()["remaining_g"] == 300

    # DELETE on unmarked meal -> 204
    del_ok = await client.delete(f"/nutrition/menu-items/{item_id}")
    assert del_ok.status_code == 204
    day_after = (await client.get(f"/nutrition/menus/{date_str}")).json()
    assert len(day_after["menus"][0]["items"]) == 0


@pytest.mark.asyncio
async def test_menu_unique_date_meal_type_constraint(client: AsyncClient):
    # Test that duplicate (user_id, date, meal_type) is rejected with 409
    await client.post("/nutrition/menus/2026-10-29/create")
    resp1 = await client.post(
        "/nutrition/menus/2026-10-29/meals",
        json={"meal_type": "afternoon_snack", "time": "16:00"},
    )
    assert resp1.status_code == 201

    # Attempting to create same meal_type again on same date -> 409
    resp2 = await client.post(
        "/nutrition/menus/2026-10-29/meals",
        json={"meal_type": "afternoon_snack", "time": "16:30"},
    )
    assert resp2.status_code == 409
    assert "already exists" in resp2.json()["detail"].lower()


@pytest.mark.asyncio
async def test_constant_dish_reserve_and_available_stock(client: AsyncClient):
    # Product with stock_g = 500
    prod_resp = await client.post(
        "/nutrition/products",
        json={"name": "Вівсяні пластівці", "stock_g": 500},
    )
    assert prod_resp.status_code == 201
    prod_id = prod_resp.json()["id"]
    assert prod_resp.json()["reserved_g"] == 0
    assert prod_resp.json()["available_g"] == 500

    # Constant dish requiring 100g of the product
    dish_resp = await client.post(
        "/nutrition/dishes",
        json={
            "name": "Вівсянка звичайна",
            "kind": "constant",
            "components": [{"product_id": prod_id, "weight_g": 100}],
        },
    )
    assert dish_resp.status_code == 201
    dish_id = dish_resp.json()["id"]

    today = dt_date.today()
    today_str = today.isoformat()
    tomorrow_str = (today + timedelta(days=1)).isoformat()
    yesterday_str = (today - timedelta(days=1)).isoformat()

    await client.post(f"/nutrition/menus/{today_str}/create")
    await client.post(f"/nutrition/menus/{tomorrow_str}/create")
    await client.post(f"/nutrition/menus/{yesterday_str}/create")

    # Add custom slot with late time on today so it is not missed
    await client.post(
        f"/nutrition/menus/{today_str}/meals",
        json={"meal_type": "evening_snack", "time": "23:59"},
    )

    # 1. Add item to today's evening_snack -> reserves 100g
    item1_resp = await client.post(
        f"/nutrition/menus/{today_str}/items?meal_type=evening_snack",
        json={"dish_id": dish_id, "portion_g": 200},
    )
    assert item1_resp.status_code == 201
    p = (await client.get(f"/nutrition/products/{prod_id}")).json()
    assert p["stock_g"] == 500
    assert p["reserved_g"] == 100
    assert p["available_g"] == 400

    # 2. Add item to tomorrow's breakfast -> reserves another 100g (total 200g)
    item2_resp = await client.post(
        f"/nutrition/menus/{tomorrow_str}/items?meal_type=breakfast",
        json={"dish_id": dish_id, "portion_g": 200},
    )
    assert item2_resp.status_code == 201
    p = (await client.get(f"/nutrition/products/{prod_id}")).json()
    assert p["stock_g"] == 500
    assert p["reserved_g"] == 200
    assert p["available_g"] == 300

    # 3. Add item to yesterday's breakfast -> past date does NOT reserve
    item3_resp = await client.post(
        f"/nutrition/menus/{yesterday_str}/items?meal_type=breakfast",
        json={"dish_id": dish_id, "portion_g": 200},
    )
    assert item3_resp.status_code == 201
    p = (await client.get(f"/nutrition/products/{prod_id}")).json()
    assert p["stock_g"] == 500
    assert p["reserved_g"] == 200
    assert p["available_g"] == 300


@pytest.mark.asyncio
async def test_constant_dish_shortage_returns_409(client: AsyncClient):
    # Product with limited stock_g = 150
    prod_resp = await client.post(
        "/nutrition/products",
        json={"name": "Сир домашній", "stock_g": 150},
    )
    assert prod_resp.status_code == 201
    prod_id = prod_resp.json()["id"]

    # Constant dish using 100g of the product
    dish_resp = await client.post(
        "/nutrition/dishes",
        json={
            "name": "Сирна запіканка",
            "kind": "constant",
            "components": [{"product_id": prod_id, "weight_g": 100}],
        },
    )
    assert dish_resp.status_code == 201
    dish_id = dish_resp.json()["id"]

    tomorrow_str = (dt_date.today() + timedelta(days=1)).isoformat()
    await client.post(f"/nutrition/menus/{tomorrow_str}/create")

    # First item takes 100g -> available becomes 50g
    add1 = await client.post(
        f"/nutrition/menus/{tomorrow_str}/items?meal_type=breakfast",
        json={"dish_id": dish_id, "portion_g": 150},
    )
    assert add1.status_code == 201

    # Second item needs 100g, but only 50g available -> 409 Conflict naming the product
    add2 = await client.post(
        f"/nutrition/menus/{tomorrow_str}/items?meal_type=dinner",
        json={"dish_id": dish_id, "portion_g": 150},
    )
    assert add2.status_code == 409
    assert "Сир домашній" in add2.json()["detail"]

    # Add free-text item then try to patch to this constant dish -> 409
    free_item = (
        await client.post(
            f"/nutrition/menus/{tomorrow_str}/items?meal_type=dinner",
            json={"title": "Чай", "portion_g": 200},
        )
    ).json()

    patch_refused = await client.patch(
        f"/nutrition/menu-items/{free_item['id']}",
        json={"dish_id": dish_id},
    )
    assert patch_refused.status_code == 409
    assert "Сир домашній" in patch_refused.json()["detail"]


@pytest.mark.asyncio
async def test_constant_dish_eaten_writeoff_and_restore(client: AsyncClient):
    prod_resp = await client.post(
        "/nutrition/products",
        json={"name": "Гречана крупа", "stock_g": 500},
    )
    assert prod_resp.status_code == 201
    prod_id = prod_resp.json()["id"]

    dish_resp = await client.post(
        "/nutrition/dishes",
        json={
            "name": "Гречана каша звичайна",
            "kind": "constant",
            "components": [{"product_id": prod_id, "weight_g": 100}],
        },
    )
    dish_id = dish_resp.json()["id"]

    tomorrow_str = (dt_date.today() + timedelta(days=1)).isoformat()
    await client.post(f"/nutrition/menus/{tomorrow_str}/create")

    # Add item to tomorrow's lunch
    item_resp = await client.post(
        f"/nutrition/menus/{tomorrow_str}/items?meal_type=lunch",
        json={"dish_id": dish_id, "portion_g": 200},
    )
    assert item_resp.status_code == 201
    menu_id = item_resp.json()["menu_id"]

    p = (await client.get(f"/nutrition/products/{prod_id}")).json()
    assert p["stock_g"] == 500
    assert p["reserved_g"] == 100
    assert p["available_g"] == 400

    # Mark meal eaten -> subtracts component weights from product stock
    mark_res = await client.patch(
        f"/nutrition/menus/{menu_id}",
        json={"eaten_at": f"{tomorrow_str}T13:00:00Z"},
    )
    assert mark_res.status_code == 200

    p_eaten = (await client.get(f"/nutrition/products/{prod_id}")).json()
    assert p_eaten["stock_g"] == 400
    assert p_eaten["reserved_g"] == 0
    assert p_eaten["available_g"] == 400

    # Unmark meal -> returns weights to product stock and restores reserve
    unmark_res = await client.patch(
        f"/nutrition/menus/{menu_id}",
        json={"eaten_at": None},
    )
    assert unmark_res.status_code == 200

    p_restored = (await client.get(f"/nutrition/products/{prod_id}")).json()
    assert p_restored["stock_g"] == 500
    assert p_restored["reserved_g"] == 100
    assert p_restored["available_g"] == 400


@pytest.mark.asyncio
async def test_meal_nutrition_snapshot_on_eaten_and_clear_on_unmark(client: AsyncClient):
    prod_resp = await client.post(
        "/nutrition/products",
        json={
            "name": "Куряче філе",
            "stock_g": 1000,
            "kcal": 165.0,
            "protein_g": 31.0,
            "fat_g": 3.6,
            "carbs_g": 0.0,
            "fiber_g": None,
            "sugar_g": None,
            "sodium_mg": 74.0,
        },
    )
    assert prod_resp.status_code == 201
    prod_id = prod_resp.json()["id"]

    date_str = (dt_date.today() + timedelta(days=2)).isoformat()
    await client.post(f"/nutrition/menus/{date_str}/create")

    dish_resp = await client.post(
        "/nutrition/dishes",
        json={
            "name": "Варене філе",
            "kind": "ready",
            "cooked_weight_g": 200,
            "cooked_on": date_str,
            "components": [{"product_id": prod_id, "weight_g": 200}],
        },
    )
    assert dish_resp.status_code == 201
    dish_id = dish_resp.json()["id"]

    # Add dish item: 100g portion -> kcal = 165.0
    dish_item = (
        await client.post(
            f"/nutrition/menus/{date_str}/items?meal_type=dinner",
            json={"dish_id": dish_id, "portion_g": 100},
        )
    ).json()
    menu_id = dish_item["menu_id"]

    # Add free-text item with user kcal
    free_item = (
        await client.post(
            f"/nutrition/menus/{date_str}/items?meal_type=dinner",
            json={"title": "Салат капустяний", "portion_g": 150, "kcal": 45.0},
        )
    ).json()

    # While planned (uneaten), dish item has computed values
    day_planned = (await client.get(f"/nutrition/menus/{date_str}")).json()
    dinner = next(m for m in day_planned["menus"] if m["meal_type"] == "dinner")
    d_it = next(it for it in dinner["items"] if it["id"] == dish_item["id"])
    f_it = next(it for it in dinner["items"] if it["id"] == free_item["id"])
    assert d_it["nutrition_source"] == "computed"
    assert d_it["kcal"] == 165.0
    assert d_it["protein_g"] == 31.0
    assert f_it["nutrition_source"] == "user"
    assert f_it["kcal"] == 45.0

    # Mark meal eaten -> stores snapshot
    mark_res = await client.patch(
        f"/nutrition/menus/{menu_id}",
        json={"eaten_at": f"{date_str}T19:00:00Z"},
    )
    assert mark_res.status_code == 200

    day_eaten = (await client.get(f"/nutrition/menus/{date_str}")).json()
    dinner_eaten = next(m for m in day_eaten["menus"] if m["meal_type"] == "dinner")
    d_it_eaten = next(it for it in dinner_eaten["items"] if it["id"] == dish_item["id"])
    f_it_eaten = next(it for it in dinner_eaten["items"] if it["id"] == free_item["id"])
    assert d_it_eaten["nutrition_source"] == "computed"
    assert d_it_eaten["kcal"] == 165.0
    assert d_it_eaten["protein_g"] == 31.0
    assert d_it_eaten["updated_at"] is not None
    assert f_it_eaten["nutrition_source"] == "user"
    assert f_it_eaten["kcal"] == 45.0

    # Delete the dish while meal is eaten: snapshot remains intact on eaten item!
    del_dish = await client.delete(f"/nutrition/dishes/{dish_id}")
    assert del_dish.status_code == 204

    day_after_del = (await client.get(f"/nutrition/menus/{date_str}")).json()
    dinner_del = next(m for m in day_after_del["menus"] if m["meal_type"] == "dinner")
    d_it_del = next(it for it in dinner_del["items"] if it["id"] == dish_item["id"])
    assert d_it_del["kcal"] == 165.0
    assert d_it_del["protein_g"] == 31.0

    # Unmark meal -> snapshot is cleared for computed items, but free-text item keeps values!
    unmark_res = await client.patch(
        f"/nutrition/menus/{menu_id}",
        json={"eaten_at": None},
    )
    assert unmark_res.status_code == 200

    day_unmarked = (await client.get(f"/nutrition/menus/{date_str}")).json()
    dinner_unmarked = next(m for m in day_unmarked["menus"] if m["meal_type"] == "dinner")
    d_it_unmarked = next(it for it in dinner_unmarked["items"] if it["id"] == dish_item["id"])
    f_it_unmarked = next(it for it in dinner_unmarked["items"] if it["id"] == free_item["id"])
    assert d_it_unmarked["kcal"] is None
    assert d_it_unmarked["nutrition_source"] is None
    assert f_it_unmarked["nutrition_source"] == "user"
    assert f_it_unmarked["kcal"] == 45.0
