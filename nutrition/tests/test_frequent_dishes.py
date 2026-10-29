from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from gemini_infra import GeminiResult


@pytest.fixture(autouse=True)
def _mock_gemini_default():
    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as m:
        m.return_value = GeminiResult(
            data={},
            raw_text="{}",
            model="gemini-2.0-flash",
            input_tokens=10,
            output_tokens=10,
        )
        yield m


@pytest.mark.asyncio
async def test_frequent_dish_crud_and_extras(client: AsyncClient):
    p1 = (await client.post("/nutrition/products", json={"name": "Вівсянка"})).json()
    await client.patch(f"/nutrition/products/{p1['id']}", json={"kcal": 350.0})

    p2 = (await client.post("/nutrition/products", json={"name": "Мед (екстра)"})).json()
    await client.patch(f"/nutrition/products/{p2['id']}", json={"kcal": 300.0})

    # Recipe includes only p1
    r = (
        await client.post(
            "/nutrition/recipes",
            json={"name": "Базова вівсянка", "product_ids": [p1["id"]]},
        )
    ).json()

    # Frequent dish includes p1 and extra product p2
    # 100g oats (350 kcal) + 20g honey (60 kcal) = 410 kcal.
    # Cooked weight = 205g.
    # Per 100g cooked = 410 * (100 / 205) = 200.0 kcal.
    fd_resp = await client.post(
        "/nutrition/frequent-dishes",
        json={
            "recipe_id": r["id"],
            "cooked_weight_g": 205,
            "components": [
                {"product_id": p1["id"], "weight_g": 100},
                {"product_id": p2["id"], "weight_g": 20},
            ],
        },
    )
    assert fd_resp.status_code == 201
    fd_data = fd_resp.json()
    fd_id = fd_data["id"]
    assert fd_data["recipe_name"] == "Базова вівсянка"
    assert fd_data["cooked_weight_g"] == 205
    assert fd_data["kcal"] == 200.0
    assert fd_data["nutrition_overridden"] is False
    assert len(fd_data["components"]) == 2

    # GET single
    get_resp = await client.get(f"/nutrition/frequent-dishes/{fd_id}")
    assert get_resp.status_code == 200
    assert get_resp.json() == fd_data

    # GET list
    list_resp = await client.get("/nutrition/frequent-dishes")
    assert list_resp.status_code == 200
    assert len(list_resp.json()) >= 1

    # DELETE
    del_resp = await client.delete(f"/nutrition/frequent-dishes/{fd_id}")
    assert del_resp.status_code == 204
    assert (await client.get(f"/nutrition/frequent-dishes/{fd_id}")).status_code == 404


@pytest.mark.asyncio
async def test_frequent_dish_override_and_reset(client: AsyncClient):
    p = (await client.post("/nutrition/products", json={"name": "Рис"})).json()
    await client.patch(f"/nutrition/products/{p['id']}", json={"kcal": 130.0})

    r = (
        await client.post(
            "/nutrition/recipes",
            json={"name": "Варений рис", "product_ids": [p["id"]]},
        )
    ).json()

    fd = (
        await client.post(
            "/nutrition/frequent-dishes",
            json={
                "recipe_id": r["id"],
                "cooked_weight_g": 100,
                "components": [{"product_id": p["id"], "weight_g": 100}],
            },
        )
    ).json()
    assert fd["kcal"] == 130.0
    assert fd["nutrition_overridden"] is False

    # 1. PATCH explicit nutrition -> stores them and sets nutrition_overridden=True
    patch_resp = await client.patch(
        f"/nutrition/frequent-dishes/{fd['id']}",
        json={"kcal": 250.0},
    )
    assert patch_resp.status_code == 200
    patched = patch_resp.json()
    assert patched["kcal"] == 250.0
    assert patched["nutrition_overridden"] is True

    # 2. PATCH {"reset_nutrition": true} -> recomputes and clears the flag
    reset_resp = await client.patch(
        f"/nutrition/frequent-dishes/{fd['id']}",
        json={"reset_nutrition": True},
    )
    assert reset_resp.status_code == 200
    reset_data = reset_resp.json()
    assert reset_data["kcal"] == 130.0
    assert reset_data["nutrition_overridden"] is False


@pytest.mark.asyncio
async def test_recompute_frequent_dishes_on_product_change(client: AsyncClient):
    p = (await client.post("/nutrition/products", json={"name": "Гречка"})).json()
    await client.patch(f"/nutrition/products/{p['id']}", json={"kcal": 100.0})

    r = (
        await client.post(
            "/nutrition/recipes",
            json={"name": "Гречка рецепт", "product_ids": [p["id"]]},
        )
    ).json()

    # Frequent dish 1: normal (non-overridden)
    fd1 = (
        await client.post(
            "/nutrition/frequent-dishes",
            json={
                "recipe_id": r["id"],
                "cooked_weight_g": 100,
                "components": [{"product_id": p["id"], "weight_g": 100}],
            },
        )
    ).json()
    assert fd1["kcal"] == 100.0

    # Frequent dish 2: overridden
    fd2 = (
        await client.post(
            "/nutrition/frequent-dishes",
            json={
                "recipe_id": r["id"],
                "cooked_weight_g": 100,
                "components": [{"product_id": p["id"], "weight_g": 100}],
            },
        )
    ).json()
    await client.patch(f"/nutrition/frequent-dishes/{fd2['id']}", json={"kcal": 999.0})

    # Update product nutrition
    await client.patch(f"/nutrition/products/{p['id']}", json={"kcal": 200.0})

    # fd1 should be recomputed to 200.0
    fd1_after = (await client.get(f"/nutrition/frequent-dishes/{fd1['id']}")).json()
    assert fd1_after["kcal"] == 200.0

    # fd2 (overridden) should remain 999.0
    fd2_after = (await client.get(f"/nutrition/frequent-dishes/{fd2['id']}")).json()
    assert fd2_after["kcal"] == 999.0


@pytest.mark.asyncio
async def test_cook_endpoint_creates_dish_with_stock_warnings(client: AsyncClient):
    test_day = datetime.now(UTC).date() + timedelta(days=2)
    p = (
        await client.post(
            "/nutrition/products",
            json={"name": "Лосось", "stock_g": 100},
        )
    ).json()
    await client.patch(f"/nutrition/products/{p['id']}", json={"kcal": 200.0})

    r = (
        await client.post(
            "/nutrition/recipes",
            json={"name": "Лосось запечений", "product_ids": [p["id"]]},
        )
    ).json()

    fd = (
        await client.post(
            "/nutrition/frequent-dishes",
            json={
                "recipe_id": r["id"],
                "cooked_weight_g": 150,
                "components": [{"product_id": p["id"], "weight_g": 150}],
            },
        )
    ).json()

    # Cook with shortage (has 100g in stock, needs 150g)
    cook_resp = await client.post(
        f"/nutrition/frequent-dishes/{fd['id']}/cook",
        json={"cooked_on": str(test_day)},
    )
    assert cook_resp.status_code == 201
    dish_data = cook_resp.json()

    # Created dish checks
    assert dish_data["name"] == "Лосось запечений"
    assert dish_data["recipe_id"] == r["id"]
    assert dish_data["cooked_weight_g"] == 150
    assert dish_data["remaining_g"] == 150
    assert dish_data["cooked_on"] == str(test_day)
    assert len(dish_data["components"]) == 1
    assert dish_data["components"][0]["product_id"] == p["id"]
    assert dish_data["components"][0]["weight_g"] == 150

    # Stock warnings check
    assert len(dish_data["stock_warnings"]) == 1
    assert dish_data["stock_warnings"][0]["product_id"] == p["id"]
    assert dish_data["stock_warnings"][0]["product_name"] == "Лосось"
    assert dish_data["stock_warnings"][0]["missing_g"] == 50

    # Stock is subtracted down to 0
    p_check = (await client.get(f"/nutrition/products/{p['id']}")).json()
    assert p_check["stock_g"] == 0
