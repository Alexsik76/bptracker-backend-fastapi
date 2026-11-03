from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from httpx import AsyncClient

from nutrition.clock import utc_today


@pytest.mark.asyncio
async def test_utc_today():
    assert utc_today() == datetime.now(UTC).date()


@pytest.mark.asyncio
async def test_menu_crud_flow(client: AsyncClient):
    today = utc_today()
    today_dt = datetime.now(UTC)

    # 1. Create a ready dish with nutrition
    dish_resp = await client.post(
        "/nutrition/dishes",
        json={
            "name": "Вівсянка",
            "is_ready": True,
            "kcal": 120.0,
            "protein_g": 4.0,
            "fat_g": 2.5,
            "carbs_g": 20.0,
        },
    )
    assert dish_resp.status_code == 201
    dish_id = dish_resp.json()["id"]

    # 2. Create eaten meal with item
    meal_payload = {
        "meal_type": "breakfast",
        "origin": "manual",
        "state": "eaten",
        "eaten_at": today_dt.isoformat(),
        "items": [
            {
                "dish_id": dish_id,
                "portion_g": 200,
            }
        ],
    }
    meal_resp = await client.post("/nutrition/menus", json=meal_payload)
    assert meal_resp.status_code == 201
    meal_data = meal_resp.json()
    meal_id = meal_data["id"]
    assert meal_data["meal_type"] == "breakfast"
    assert meal_data["origin"] == "manual"
    assert meal_data["state"] == "eaten"
    assert len(meal_data["items"]) == 1
    item = meal_data["items"][0]
    item_id = item["id"]
    assert item["title"] == "Вівсянка"
    assert item["portion_g"] == 200
    assert item["portion_text"] == "200 г"
    # Ratio = 200/100 = 2.0 -> kcal = 120*2 = 240
    assert item["kcal"] == 240.0
    assert item["protein_g"] == 8.0
    assert item["nutrition_source"] == "computed"

    # 3. Create another meal with the SAME meal_type on the same day (no uniqueness restriction!)
    second_meal_payload = {
        "meal_type": "breakfast",
        "origin": "manual",
        "state": "eaten",
        "eaten_at": (today_dt + timedelta(hours=2)).isoformat(),
        "items": [
            {
                "title": "Кава з молоком",
                "portion_g": 150,
                "kcal": 50.0,
            }
        ],
    }
    second_resp = await client.post("/nutrition/menus", json=second_meal_payload)
    assert second_resp.status_code == 201
    assert second_resp.json()["meal_type"] == "breakfast"

    # 4. Create a suggestion meal (eaten_at is None)
    sugg_payload = {
        "meal_type": "lunch",
        "origin": "suggestion",
        "state": "suggestion",
        "eaten_at": None,
        "items": [
            {
                "dish_id": dish_id,
                "portion_g": 300,
            }
        ],
    }
    sugg_resp = await client.post("/nutrition/menus", json=sugg_payload)
    assert sugg_resp.status_code == 201
    sugg_data = sugg_resp.json()
    assert sugg_data["state"] == "suggestion"
    assert sugg_data["eaten_at"] is None

    # 5. List meals for today: should include both eaten meals and the current suggestion
    list_resp = await client.get(f"/nutrition/menus?date={today.isoformat()}")
    assert list_resp.status_code == 200
    day_meals = list_resp.json()
    assert len(day_meals) == 3
    ids = [m["id"] for m in day_meals]
    assert meal_id in ids
    assert sugg_data["id"] in ids

    # 6. Get single meal
    get_meal_resp = await client.get(f"/nutrition/menus/{meal_id}")
    assert get_meal_resp.status_code == 200
    assert get_meal_resp.json()["id"] == meal_id

    # 7. Change grams of a menu item with recomputed snapshot
    patch_item_resp = await client.patch(
        f"/nutrition/menu-items/{item_id}",
        json={"portion_g": 300},
    )
    assert patch_item_resp.status_code == 200
    patched_item = patch_item_resp.json()
    assert patched_item["portion_g"] == 300
    assert patched_item["portion_text"] == "300 г"
    # Ratio = 300/100 = 3.0 -> kcal = 120*3 = 360
    assert patched_item["kcal"] == 360.0
    assert patched_item["protein_g"] == 12.0
    assert patched_item["nutrition_source"] == "computed"

    # 8. Delete menu item
    del_item_resp = await client.delete(f"/nutrition/menu-items/{item_id}")
    assert del_item_resp.status_code == 204
    # Check item is gone from meal
    meal_after_item_del = (await client.get(f"/nutrition/menus/{meal_id}")).json()
    assert len(meal_after_item_del["items"]) == 0

    # 9. Delete meal
    del_meal_resp = await client.delete(f"/nutrition/menus/{meal_id}")
    assert del_meal_resp.status_code == 204
    assert (await client.get(f"/nutrition/menus/{meal_id}")).status_code == 404


@pytest.mark.asyncio
async def test_recompute_snapshot_for_free_text_item(client: AsyncClient):
    # Free-text item with initial kcal and grams
    meal = (
        await client.post(
            "/nutrition/menus",
            json={
                "meal_type": "afternoon_snack",
                "items": [
                    {
                        "title": "Печиво",
                        "portion_g": 50,
                        "kcal": 200.0,
                        "protein_g": 4.0,
                    }
                ],
            },
        )
    ).json()
    item_id = meal["items"][0]["id"]

    # Change grams to 100g -> ratio = 100/50 = 2.0 -> kcal=400, protein=8
    patch_resp = await client.patch(
        f"/nutrition/menu-items/{item_id}",
        json={"portion_g": 100},
    )
    assert patch_resp.status_code == 200
    data = patch_resp.json()
    assert data["portion_g"] == 100
    assert data["kcal"] == 400.0
    assert data["protein_g"] == 8.0


@pytest.mark.asyncio
async def test_menu_user_isolation(client_factory, make_user):
    user_a = await make_user("ma@example.com")
    user_b = await make_user("mb@example.com")

    client_a = client_factory(user_a)

    meal_a = (
        await client_a.post(
            "/nutrition/menus",
            json={
                "meal_type": "lunch",
                "items": [{"title": "Суп", "portion_g": 250, "kcal": 150.0}],
            },
        )
    ).json()
    meal_a_id = meal_a["id"]
    item_a_id = meal_a["items"][0]["id"]

    client_b = client_factory(user_b)

    # User B cannot see or touch User A's meal or item
    list_b = (await client_b.get("/nutrition/menus")).json()
    assert not any(m["id"] == meal_a_id for m in list_b)
    assert (await client_b.get(f"/nutrition/menus/{meal_a_id}")).status_code == 404
    assert (await client_b.delete(f"/nutrition/menus/{meal_a_id}")).status_code == 404
    assert (
        await client_b.patch(f"/nutrition/menu-items/{item_a_id}", json={"portion_g": 300})
    ).status_code == 404
    assert (await client_b.delete(f"/nutrition/menu-items/{item_a_id}")).status_code == 404


@pytest.mark.asyncio
async def test_not_found_returns_404(client: AsyncClient):
    random_id = uuid4()
    assert (await client.get(f"/nutrition/menus/{random_id}")).status_code == 404
    assert (await client.delete(f"/nutrition/menus/{random_id}")).status_code == 404
    patch_meal = await client.patch(f"/nutrition/menus/{random_id}", json={"state": "eaten"})
    assert patch_meal.status_code == 404
    patch_item = await client.patch(
        f"/nutrition/menu-items/{random_id}", json={"portion_g": 100}
    )
    assert patch_item.status_code == 404
    assert (await client.delete(f"/nutrition/menu-items/{random_id}")).status_code == 404
