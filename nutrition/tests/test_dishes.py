from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
from uuid import uuid4

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
async def test_create_dish_with_new_recipe_shorthand(client: AsyncClient):
    test_day = datetime.now(UTC).date() + timedelta(days=2)
    p1 = (
        await client.post(
            "/nutrition/products", json={"name": "Вівсяні пластівці", "stock_g": 500}
        )
    ).json()
    p2 = (
        await client.post(
            "/nutrition/products", json={"name": "Яблуко", "stock_g": 500}
        )
    ).json()

    payload = {
        "new_recipe": {"name": "Ранкова вівсянка"},
        "cooked_weight_g": 300,
        "cooked_on": str(test_day),
        "notes": "Базова страва",
        "components": [
            {"product_id": p1["id"], "weight_g": 45},
            {"product_id": p2["id"], "weight_g": 100},
            {"new_product": {"name": "Сир кисломолочний 5%", "stock_g": 200}, "weight_g": 150},
        ],
    }
    resp = await client.post("/nutrition/dishes", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    dish_id = data["id"]
    assert data["name"] == "Ранкова вівсянка"
    assert data["recipe_id"] is not None
    assert data["cooked_weight_g"] == 300
    assert data["remaining_g"] == 300
    assert data["stock_warnings"] == []
    assert len(data["components"]) == 3
    comp_product_ids = {c["product_id"] for c in data["components"]}
    assert p1["id"] in comp_product_ids
    assert p2["id"] in comp_product_ids

    # Fetch by ID
    get_resp = await client.get(f"/nutrition/dishes/{dish_id}")
    assert get_resp.status_code == 200
    assert get_resp.json() == data


@pytest.mark.asyncio
async def test_create_dish_with_existing_recipe(client: AsyncClient):
    # Create product and recipe first
    test_day = datetime.now(UTC).date() + timedelta(days=2)
    p1 = (
        await client.post(
            "/nutrition/products", json={"name": "Індичка", "stock_g": 1000}
        )
    ).json()
    rec_resp = await client.post(
        "/nutrition/recipes",
        json={"name": "Філе індички", "product_ids": [p1["id"]]},
    )
    assert rec_resp.status_code == 201
    recipe_id = rec_resp.json()["id"]

    payload = {
        "recipe_id": recipe_id,
        "cooked_weight_g": 600,
        "cooked_on": str(test_day),
        "components": [
            {"product_id": p1["id"], "weight_g": 400},
        ],
    }
    resp = await client.post("/nutrition/dishes", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Філе індички"
    assert data["recipe_id"] == recipe_id
    assert data["cooked_weight_g"] == 600
    assert data["remaining_g"] == 600
    assert data["cooked_on"] == str(test_day)
    assert len(data["components"]) == 1
    assert data["stock_warnings"] == []


@pytest.mark.asyncio
async def test_dish_validation():
    from nutrition.models import DishComponentCreate, DishCreate, DishUpdate

    # cooked_weight_g must be > 0
    with pytest.raises(ValueError, match="cooked_weight_g must be > 0"):
        DishCreate(name="Риба", cooked_weight_g=0)

    # cooked_weight_g cannot be negative
    with pytest.raises(ValueError, match="cooked_weight_g must be > 0"):
        DishCreate(name="Риба", cooked_weight_g=-100)

    # Negative remaining_g raises ValueError
    with pytest.raises(ValueError, match="remaining_g cannot be negative"):
        DishCreate(name="Риба", cooked_weight_g=300, remaining_g=-50)

    # DishUpdate validates negative remaining_g
    with pytest.raises(ValueError, match="remaining_g cannot be negative"):
        DishUpdate(remaining_g=-10)

    # DishUpdate validates cooked_weight_g > 0
    with pytest.raises(ValueError, match="cooked_weight_g must be > 0"):
        DishUpdate(cooked_weight_g=0)

    # Component validation: weight_g must be > 0
    with pytest.raises(ValueError, match="weight_g must be > 0"):
        DishComponentCreate(product_id=uuid4(), weight_g=0)

    # Component validation: either product_id or new_product
    with pytest.raises(ValueError, match="Either product_id or new_product must be provided"):
        DishComponentCreate(weight_g=100)


@pytest.mark.asyncio
async def test_dish_computed_nutrition(client: AsyncClient):
    # Set explicit nutrition via product PATCH
    p1 = (await client.post("/nutrition/products", json={"name": "Вівсянка чиста"})).json()
    await client.patch(
        f"/nutrition/products/{p1['id']}",
        json={"kcal": 350.0, "protein_g": 12.0, "fat_g": 6.0, "carbs_g": 60.0},
    )

    p2 = (await client.post("/nutrition/products", json={"name": "Молоко 2.5%"})).json()
    await client.patch(
        f"/nutrition/products/{p2['id']}",
        json={"kcal": 60.0, "protein_g": 3.0, "fat_g": 2.5, "carbs_g": 5.0},
    )

    # 100g oats (350 kcal) + 200g milk (120 kcal) = 470 kcal total raw.
    # Cooked weight = 235g.
    # Per 100g cooked = 470 * (100 / 235) = 200.0 kcal.
    resp = await client.post(
        "/nutrition/dishes",
        json={
            "name": "Готова каша на молоці",
            "cooked_weight_g": 235,
            "components": [
                {"product_id": p1["id"], "weight_g": 100},
                {"product_id": p2["id"], "weight_g": 200},
            ],
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["kcal"] == 200.0
    # 100g oats (12g protein) + 200g milk (6g protein) = 18g protein total raw.
    # Per 100g cooked = 18 * (100 / 235) = 7.7
    assert data["protein_g"] == 7.7
    # Missing fields are None, never 0
    assert data["fiber_g"] is None


@pytest.mark.asyncio
async def test_dish_stock_deduction_and_return(client: AsyncClient):
    # 1. Product with 500g stock
    p = (
        await client.post(
            "/nutrition/products",
            json={"name": "Філе індички", "stock_g": 500},
        )
    ).json()

    # 2. Create dish with 200g of product
    d_resp = await client.post(
        "/nutrition/dishes",
        json={
            "name": "Індичка запечена",
            "cooked_weight_g": 180,
            "components": [{"product_id": p["id"], "weight_g": 200}],
        },
    )
    assert d_resp.status_code == 201
    dish_id = d_resp.json()["id"]
    assert d_resp.json()["stock_warnings"] == []

    # Product stock should be 500 - 200 = 300
    p_after = (await client.get(f"/nutrition/products/{p['id']}")).json()
    assert p_after["stock_g"] == 300

    # 3. Delete dish -> stock returns to 500
    del_resp = await client.delete(f"/nutrition/dishes/{dish_id}")
    assert del_resp.status_code == 204
    p_restored = (await client.get(f"/nutrition/products/{p['id']}")).json()
    assert p_restored["stock_g"] == 500


@pytest.mark.asyncio
async def test_dish_shortage_stock_warnings(client: AsyncClient):
    p = (
        await client.post(
            "/nutrition/products",
            json={"name": "Лосось", "stock_g": 150},
        )
    ).json()

    # Attempt to create dish requiring 200g: stock rule never fails on shortage!
    # Subtracts tracked stock down to 0 and returns stock_warnings.
    resp = await client.post(
        "/nutrition/dishes",
        json={
            "name": "Лосось на пару",
            "cooked_weight_g": 180,
            "components": [{"product_id": p["id"], "weight_g": 200}],
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert len(data["stock_warnings"]) == 1
    assert data["stock_warnings"][0]["product_id"] == p["id"]
    assert data["stock_warnings"][0]["product_name"] == "Лосось"
    assert data["stock_warnings"][0]["missing_g"] == 50

    p_after = (await client.get(f"/nutrition/products/{p['id']}")).json()
    assert p_after["stock_g"] == 0


@pytest.mark.asyncio
async def test_dish_component_change_delta(client: AsyncClient):
    # p1: 100 kcal / 100g (1 kcal/g)
    p1 = (
        await client.post(
            "/nutrition/products",
            json={"name": "Продукт 1", "stock_g": 1000},
        )
    ).json()
    await client.patch(f"/nutrition/products/{p1['id']}", json={"kcal": 100.0})

    # p2: 200 kcal / 100g (2 kcal/g)
    p2 = (
        await client.post(
            "/nutrition/products",
            json={"name": "Продукт 2", "stock_g": 1000},
        )
    ).json()
    await client.patch(f"/nutrition/products/{p2['id']}", json={"kcal": 200.0})

    # Create dish: 100g of p1. Cooked weight = 100g.
    # Raw kcal = 100. Per 100g cooked = 100 kcal.
    dish = (
        await client.post(
            "/nutrition/dishes",
            json={
                "name": "Тестова страва",
                "cooked_weight_g": 100,
                "components": [{"product_id": p1["id"], "weight_g": 100}],
            },
        )
    ).json()
    assert dish["kcal"] == 100.0

    # Stock of p1 after create: 1000 - 100 = 900
    p1_check = (await client.get(f"/nutrition/products/{p1['id']}")).json()
    assert p1_check["stock_g"] == 900

    # Patch components to 100g of p2.
    # Computed old = 100.0, computed new = 200.0.
    # Stored nutrition = stored + (computed(new) - computed(old)) = 100 + (200 - 100) = 200.0.
    patch_resp = await client.patch(
        f"/nutrition/dishes/{dish['id']}",
        json={
            "components": [{"product_id": p2["id"], "weight_g": 100}],
        },
    )
    assert patch_resp.status_code == 200
    patched_data = patch_resp.json()
    assert patched_data["kcal"] == 200.0

    # Stock of p1 restored to 1000, p2 subtracted 100 -> 900
    p1_restored = (await client.get(f"/nutrition/products/{p1['id']}")).json()
    assert p1_restored["stock_g"] == 1000
    p2_check = (await client.get(f"/nutrition/products/{p2['id']}")).json()
    assert p2_check["stock_g"] == 900

    # Explicit nutrition in the same PATCH wins
    patch_win_resp = await client.patch(
        f"/nutrition/dishes/{dish['id']}",
        json={
            "components": [{"product_id": p1["id"], "weight_g": 100}],
            "kcal": 999.0,
        },
    )
    assert patch_win_resp.status_code == 200
    assert patch_win_resp.json()["kcal"] == 999.0


@pytest.mark.asyncio
async def test_dish_filtering_availability(client: AsyncClient):
    p = (await client.post("/nutrition/products", json={"name": "Крупа ячна"})).json()

    # 1. Dish with remaining > 0
    await client.post(
        "/nutrition/dishes",
        json={
            "name": "Борщ",
            "cooked_weight_g": 800,
            "remaining_g": 400,
            "cooked_on": str(datetime.now(UTC).date()),
            "components": [{"product_id": p["id"], "weight_g": 50}],
        },
    )
    # 2. Dish with remaining = 0
    await client.post(
        "/nutrition/dishes",
        json={
            "name": "Плов",
            "cooked_weight_g": 500,
            "remaining_g": 0,
            "cooked_on": str(datetime.now(UTC).date()),
            "components": [{"product_id": p["id"], "weight_g": 50}],
        },
    )

    resp_avail = await client.get("/nutrition/dishes?available=true")
    assert resp_avail.status_code == 200
    avail_names = {d["name"] for d in resp_avail.json()}
    assert "Борщ" in avail_names
    assert "Плов" not in avail_names


@pytest.mark.asyncio
async def test_update_and_delete_dish(client: AsyncClient):
    test_day = datetime.now(UTC).date() + timedelta(days=2)
    p1 = (
        await client.post(
            "/nutrition/products", json={"name": "Курка", "stock_g": 2000}
        )
    ).json()
    p2 = (
        await client.post(
            "/nutrition/products", json={"name": "Курка тушка", "stock_g": 2000}
        )
    ).json()

    create_resp = await client.post(
        "/nutrition/dishes",
        json={
            "name": "Курка запечена",
            "cooked_weight_g": 800,
            "cooked_on": str(test_day),
            "components": [{"product_id": p1["id"], "weight_g": 1000}],
        },
    )
    assert create_resp.status_code == 201
    dish_id = create_resp.json()["id"]

    # Update dish notes, remaining_g and replace components
    patch_resp = await client.patch(
        f"/nutrition/dishes/{dish_id}",
        json={
            "remaining_g": 400,
            "notes": "Половину з'їли",
            "components": [{"product_id": p2["id"], "weight_g": 1000}],
        },
    )
    assert patch_resp.status_code == 200
    data = patch_resp.json()
    assert data["remaining_g"] == 400
    assert data["notes"] == "Половину з'їли"
    assert len(data["components"]) == 1
    assert data["components"][0]["product_id"] == p2["id"]

    # Delete dish
    del_resp = await client.delete(f"/nutrition/dishes/{dish_id}")
    assert del_resp.status_code == 204

    # Confirm 404
    get_resp = await client.get(f"/nutrition/dishes/{dish_id}")
    assert get_resp.status_code == 404


@pytest.mark.asyncio
async def test_dish_user_isolation(client_factory, make_user):
    user_a = await make_user("user_a@example.com")
    user_b = await make_user("user_b@example.com")

    client1 = client_factory(user_a)

    # User 1 creates dish with new_product
    resp1 = await client1.post(
        "/nutrition/dishes",
        json={
            "name": "Омлет",
            "cooked_weight_g": 120,
            "components": [{"new_product": {"name": "Яйця"}, "weight_g": 120}],
        },
    )
    assert resp1.status_code == 201
    dish_id = resp1.json()["id"]

    client2 = client_factory(user_b)

    # User 2 cannot access user 1's dish
    get_resp = await client2.get(f"/nutrition/dishes/{dish_id}")
    assert get_resp.status_code == 404

    # User 2 cannot update user 1's dish
    patch_resp = await client2.patch(
        f"/nutrition/dishes/{dish_id}",
        json={"name": "Хакнутий омлет"},
    )
    assert patch_resp.status_code == 404

    # User 2 cannot delete user 1's dish
    del_resp = await client2.delete(f"/nutrition/dishes/{dish_id}")
    assert del_resp.status_code == 404

    # User 1 dish still exists
    client1_again = client_factory(user_a)
    check_resp = await client1_again.get(f"/nutrition/dishes/{dish_id}")
    assert check_resp.status_code == 200
    assert check_resp.json()["name"] == "Омлет"
