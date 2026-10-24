import pytest
from httpx import AsyncClient


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
