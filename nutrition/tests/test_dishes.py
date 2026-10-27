from datetime import date
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from httpx import AsyncClient

from gemini_infra import GeminiResult


@pytest.mark.asyncio
async def test_create_constant_dish_with_components(client: AsyncClient):
    # Create products
    p1 = (await client.post("/nutrition/products", json={"name": "Вівсяні пластівці"})).json()
    p2 = (await client.post("/nutrition/products", json={"name": "Яблуко"})).json()

    payload = {
        "name": "Ранкова вівсянка",
        "kind": "constant",
        "fixed_meal_type": "breakfast",
        "notes": "Базова страва",
        "components": [
            {"product_id": p1["id"], "weight_g": 45},
            {"product_id": p2["id"], "weight_g": 100},
            {"new_product": {"name": "Сир кисломолочний 5%"}, "weight_g": 150},
        ],
    }
    resp = await client.post("/nutrition/dishes", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    dish_id = data["id"]
    assert data["name"] == "Ранкова вівсянка"
    assert data["kind"] == "constant"
    assert data["fixed_meal_type"] == "breakfast"
    assert len(data["components"]) == 3
    comp_product_ids = {c["product_id"] for c in data["components"]}
    assert p1["id"] in comp_product_ids
    assert p2["id"] in comp_product_ids

    # Fetch by ID
    get_resp = await client.get(f"/nutrition/dishes/{dish_id}")
    assert get_resp.status_code == 200
    assert get_resp.json() == data


@pytest.mark.asyncio
async def test_create_ready_dish(client: AsyncClient):
    p1 = (
        await client.post(
            "/nutrition/products", json={"name": "Індичка", "stock_g": 1000}
        )
    ).json()
    p2 = (
        await client.post(
            "/nutrition/products", json={"name": "Кабачок", "stock_g": 1000}
        )
    ).json()

    payload = {
        "name": "Тушкована індичка з овочами",
        "kind": "ready",
        "cooked_weight_g": 600,
        "cooked_on": "2026-10-20",
        "components": [
            {"product_id": p1["id"], "weight_g": 400},
            {"product_id": p2["id"], "weight_g": 250},
        ],
    }
    resp = await client.post("/nutrition/dishes", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Тушкована індичка з овочами"
    assert data["kind"] == "ready"
    assert data["cooked_weight_g"] == 600
    # remaining_g defaults to cooked_weight_g
    assert data["remaining_g"] == 600
    assert data["cooked_on"] == "2026-10-20"
    assert len(data["components"]) == 2


@pytest.mark.asyncio
async def test_create_ready_dish_without_weight(client: AsyncClient):
    payload = {
        "name": "Кавун нарізаний",
        "kind": "ready",
    }
    resp = await client.post("/nutrition/dishes", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Кавун нарізаний"
    assert data["cooked_weight_g"] is None
    assert data["remaining_g"] is None


@pytest.mark.asyncio
async def test_dish_validation():
    from nutrition.models import DishComponentCreate, DishCreate, DishUpdate

    # Constant dish without components raises ValueError
    with pytest.raises(ValueError, match="components are required for constant dishes"):
        DishCreate(name="Салат", kind="constant", components=[])

    # Negative remaining_g raises ValueError
    with pytest.raises(ValueError, match="remaining_g cannot be negative"):
        DishCreate(name="Риба", kind="ready", cooked_weight_g=300, remaining_g=-50)

    # DishUpdate validates negative remaining_g
    with pytest.raises(ValueError, match="remaining_g cannot be negative"):
        DishUpdate(remaining_g=-10)

    # Component validation: weight_g must be > 0
    with pytest.raises(ValueError, match="weight_g must be > 0"):
        DishComponentCreate(product_id=uuid4(), weight_g=0)

    # Component validation: either product_id or new_product
    with pytest.raises(ValueError, match="Either product_id or new_product must be provided"):
        DishComponentCreate(weight_g=100)


@pytest.mark.asyncio
async def test_dish_computed_nutrition(client: AsyncClient):
    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_gemini:
        mock_gemini.return_value = GeminiResult(
            data={"kcal": 350.0, "protein_g": 12.0, "fat_g": 6.0, "carbs_g": 60.0},
            raw_text="{}",
            model="gemini-2.0-flash",
            input_tokens=10,
            output_tokens=10,
        )
        p1 = (await client.post("/nutrition/products", json={"name": "Вівсянка чиста"})).json()

        mock_gemini.return_value = GeminiResult(
            data={"kcal": 60.0, "protein_g": 3.0, "fat_g": 2.5, "carbs_g": 5.0},
            raw_text="{}",
            model="gemini-2.0-flash",
            input_tokens=10,
            output_tokens=10,
        )
        p2 = (await client.post("/nutrition/products", json={"name": "Молоко 2.5%"})).json()

    # 1. Constant dish: whole composition
    # 50g oats (175 kcal, 6 protein, 3 fat, 30 carbs) +
    # 100g milk (60 kcal, 3 protein, 2.5 fat, 5 carbs)
    # Total = 235 kcal, 9.0 protein, 5.5 fat, 35.0 carbs
    resp_const = await client.post(
        "/nutrition/dishes",
        json={
            "name": "Вівсяна каша з молоком",
            "kind": "constant",
            "components": [
                {"product_id": p1["id"], "weight_g": 50},
                {"product_id": p2["id"], "weight_g": 100},
            ],
        },
    )
    assert resp_const.status_code == 201
    const_data = resp_const.json()
    assert const_data["kcal"] == 235.0
    assert const_data["protein_g"] == 9.0
    assert const_data["fat_g"] == 5.5
    assert const_data["carbs_g"] == 35.0

    # 2. Ready dish: per 100g cooked
    # 100g oats (350 kcal) + 200g milk (120 kcal) = 470 kcal total raw.
    # Cooked weight = 235g.
    # Per 100g cooked = 470 * (100 / 235) = 200.0 kcal.
    resp_ready = await client.post(
        "/nutrition/dishes",
        json={
            "name": "Готова каша на молоці",
            "kind": "ready",
            "cooked_weight_g": 235,
            "components": [
                {"product_id": p1["id"], "weight_g": 100},
                {"product_id": p2["id"], "weight_g": 200},
            ],
        },
    )
    assert resp_ready.status_code == 201
    ready_data = resp_ready.json()
    assert ready_data["kcal"] == 200.0


@pytest.mark.asyncio
async def test_ready_dish_stock_deduction_and_return(client: AsyncClient):
    # 1. Product with 500g stock
    p = (
        await client.post(
            "/nutrition/products",
            json={"name": "Філе індички", "stock_g": 500},
        )
    ).json()

    # 2. Create ready dish with 200g of product
    d_resp = await client.post(
        "/nutrition/dishes",
        json={
            "name": "Індичка запечена",
            "kind": "ready",
            "cooked_weight_g": 180,
            "components": [{"product_id": p["id"], "weight_g": 200}],
        },
    )
    assert d_resp.status_code == 201
    dish_id = d_resp.json()["id"]

    # Product stock should be 500 - 200 = 300
    p_after = (await client.get(f"/nutrition/products/{p['id']}")).json()
    assert p_after["stock_g"] == 300

    # 3. Delete ready dish -> stock returns to 500
    del_resp = await client.delete(f"/nutrition/dishes/{dish_id}")
    assert del_resp.status_code == 204
    p_restored = (await client.get(f"/nutrition/products/{p['id']}")).json()
    assert p_restored["stock_g"] == 500


@pytest.mark.asyncio
async def test_ready_dish_shortage_returns_409(client: AsyncClient):
    p = (
        await client.post(
            "/nutrition/products",
            json={"name": "Лосось", "stock_g": 150},
        )
    ).json()

    # Attempt to create ready dish requiring 200g
    resp = await client.post(
        "/nutrition/dishes",
        json={
            "name": "Лосось на пару",
            "kind": "ready",
            "cooked_weight_g": 180,
            "components": [{"product_id": p["id"], "weight_g": 200}],
        },
    )
    assert resp.status_code == 409
    assert "Лосось" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_dish_filtering_kind_and_availability(client: AsyncClient):
    p = (await client.post("/nutrition/products", json={"name": "Крупа ячна"})).json()

    # 1. Constant dish
    await client.post(
        "/nutrition/dishes",
        json={
            "name": "Каша",
            "kind": "constant",
            "components": [{"product_id": p["id"], "weight_g": 50}],
        },
    )
    # 2. Ready dish with remaining > 0
    await client.post(
        "/nutrition/dishes",
        json={
            "name": "Борщ",
            "kind": "ready",
            "cooked_weight_g": 800,
            "remaining_g": 400,
            "cooked_on": str(date.today()),
        },
    )
    # 3. Ready dish with remaining = 0
    await client.post(
        "/nutrition/dishes",
        json={
            "name": "Плов",
            "kind": "ready",
            "cooked_weight_g": 500,
            "remaining_g": 0,
            "cooked_on": str(date.today()),
        },
    )

    resp_avail = await client.get("/nutrition/dishes?available=true")
    assert resp_avail.status_code == 200
    avail_names = {d["name"] for d in resp_avail.json()}
    assert "Каша" in avail_names
    assert "Борщ" in avail_names
    assert "Плов" not in avail_names


@pytest.mark.asyncio
async def test_update_and_delete_dish(client: AsyncClient):
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
            "kind": "ready",
            "cooked_weight_g": 800,
            "cooked_on": "2026-10-23",
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
            "kind": "constant",
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
