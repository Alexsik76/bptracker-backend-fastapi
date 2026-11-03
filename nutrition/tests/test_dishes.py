from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from httpx import AsyncClient

from gemini_infra import GeminiResult
from nutrition.models import DishComponentCreate


@pytest.fixture(autouse=True)
def _mock_gemini_default():
    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as m:
        m.return_value = GeminiResult(
            data={"kcal": 120.0, "protein_g": 10.0, "fat_g": 3.0, "carbs_g": 15.0},
            raw_text='{"kcal": 120.0, "protein_g": 10.0, "fat_g": 3.0, "carbs_g": 15.0}',
            model="gemini-2.0-flash",
            input_tokens=10,
            output_tokens=10,
        )
        yield m


@pytest.mark.asyncio
async def test_create_dish_with_only_name(client: AsyncClient):
    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_gemini:
        mock_gemini.return_value = GeminiResult(
            data={
                "kcal": 215.0,
                "protein_g": 24.0,
                "fat_g": 12.0,
                "carbs_g": 0.0,
                "fiber_g": None,
                "sugar_g": None,
                "sodium_mg": 70.0,
            },
            raw_text="{}",
            model="gemini-2.0-flash",
            input_tokens=20,
            output_tokens=20,
        )
        resp = await client.post("/nutrition/dishes", json={"name": "Філе індички запечене"})
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Філе індички запечене"
        assert data["is_ready"] is False
        assert data["pinned_meal_type"] is None
        assert data["nutrition_overridden"] is False
        assert data["kcal"] == 215.0
        assert data["protein_g"] == 24.0
        assert data["fat_g"] == 12.0
        assert data["carbs_g"] == 0.0
        assert data["sodium_mg"] == 70.0
        assert mock_gemini.call_count == 1


@pytest.mark.asyncio
async def test_create_dish_with_explicit_nutrition(client: AsyncClient):
    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_gemini:
        payload = {
            "name": "Сир домашній",
            "is_ready": True,
            "pinned_meal_type": "breakfast",
            "note": "жирний",
            "kcal": 150.0,
            "protein_g": 16.0,
            "fat_g": 9.0,
            "carbs_g": 2.0,
        }
        resp = await client.post("/nutrition/dishes", json=payload)
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Сир домашній"
        assert data["is_ready"] is True
        assert data["pinned_meal_type"] == "breakfast"
        assert data["nutrition_overridden"] is True
        assert data["note"] == "жирний"
        assert data["kcal"] == 150.0
        assert mock_gemini.call_count == 0


@pytest.mark.asyncio
async def test_create_dish_with_components(client: AsyncClient):
    p1 = (
        await client.post(
            "/nutrition/products", json={"name": "Вівсяні пластівці", "available": True}
        )
    ).json()

    payload = {
        "name": "Вівсянка з яблуком",
        "is_ready": False,
        "note": "на воді",
        "components": [
            {"product_id": p1["id"], "weight_g": 50},
            {"new_product": {"name": "Яблуко", "available": True}, "weight_g": 100},
        ],
    }
    resp = await client.post("/nutrition/dishes", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Вівсянка з яблуком"
    assert len(data["components"]) == 2
    comp_product_ids = {c["product_id"] for c in data["components"]}
    assert p1["id"] in comp_product_ids


@pytest.mark.asyncio
async def test_dish_crud_and_filters(client: AsyncClient):
    d1 = (
        await client.post(
            "/nutrition/dishes",
            json={
                "name": "Омлет",
                "is_ready": True,
                "pinned_meal_type": "breakfast",
                "kcal": 180.0,
                "protein_g": 14.0,
                "fat_g": 13.0,
                "carbs_g": 2.0,
            },
        )
    ).json()
    d2 = (
        await client.post(
            "/nutrition/dishes",
            json={
                "name": "Борщ",
                "is_ready": False,
                "kcal": 60.0,
                "protein_g": 4.0,
                "fat_g": 2.5,
                "carbs_g": 6.0,
            },
        )
    ).json()

    # Get single dish
    get_resp = await client.get(f"/nutrition/dishes/{d1['id']}")
    assert get_resp.status_code == 200
    assert get_resp.json()["name"] == "Омлет"

    # List all
    all_dishes = (await client.get("/nutrition/dishes")).json()
    assert len(all_dishes) >= 2

    # Filter is_ready=true
    ready_dishes = (await client.get("/nutrition/dishes?is_ready=true")).json()
    assert any(d["id"] == d1["id"] for d in ready_dishes)
    assert not any(d["id"] == d2["id"] for d in ready_dishes)

    # Filter pinned_meal_type=breakfast
    pinned_dishes = (await client.get("/nutrition/dishes?pinned_meal_type=breakfast")).json()
    assert any(d["id"] == d1["id"] for d in pinned_dishes)
    assert not any(d["id"] == d2["id"] for d in pinned_dishes)

    # Update dish (toggle ready, override kcal)
    patch_resp = await client.patch(
        f"/nutrition/dishes/{d2['id']}",
        json={"is_ready": True, "kcal": 65.0},
    )
    assert patch_resp.status_code == 200
    patched = patch_resp.json()
    assert patched["is_ready"] is True
    assert patched["kcal"] == 65.0
    assert patched["nutrition_overridden"] is True

    # Delete dish
    del_resp = await client.delete(f"/nutrition/dishes/{d2['id']}")
    assert del_resp.status_code == 204
    assert (await client.get(f"/nutrition/dishes/{d2['id']}")).status_code == 404


@pytest.mark.asyncio
async def test_dish_user_isolation(client_factory, make_user):
    user_a = await make_user("da@example.com")
    user_b = await make_user("db@example.com")

    client_a = client_factory(user_a)
    d = (
        await client_a.post(
            "/nutrition/dishes",
            json={
                "name": "Каша гречана",
                "kcal": 110.0,
                "protein_g": 4.0,
                "fat_g": 1.0,
                "carbs_g": 20.0,
            },
        )
    ).json()

    client_b = client_factory(user_b)
    # User B cannot see or modify User A's dish
    list_b = (await client_b.get("/nutrition/dishes")).json()
    assert not any(dish["id"] == d["id"] for dish in list_b)
    assert (await client_b.get(f"/nutrition/dishes/{d['id']}")).status_code == 404
    patch_resp = await client_b.patch(f"/nutrition/dishes/{d['id']}", json={"name": "X"})
    assert patch_resp.status_code == 404
    assert (await client_b.delete(f"/nutrition/dishes/{d['id']}")).status_code == 404


@pytest.mark.asyncio
async def test_dish_component_validation():
    with pytest.raises(ValueError, match="weight_g must be > 0"):
        DishComponentCreate(product_id=uuid4(), weight_g=0)

    with pytest.raises(ValueError, match="Either product_id or new_product must be provided"):
        DishComponentCreate(weight_g=100)
