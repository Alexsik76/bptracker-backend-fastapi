from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from httpx import AsyncClient

from gemini_infra import GeminiResult, GeminiUnavailable


@pytest.mark.asyncio
async def test_product_crud_flow(client: AsyncClient):
    # 1. Initially empty
    resp = await client.get("/nutrition/products")
    assert resp.status_code == 200
    assert resp.json() == []

    # 2. Create product
    payload = {
        "name": "Волоські горіхи",
        "availability": "in_stock",
        "preference": "preferred",
        "eat_raw": True,
        "stock_g": 500,
    }
    create_resp = await client.post("/nutrition/products", json=payload)
    assert create_resp.status_code == 201
    created = create_resp.json()
    product_id = created["id"]
    assert created["name"] == "Волоські горіхи"
    assert created["availability"] == "in_stock"
    assert created["preference"] == "preferred"
    assert created["eat_raw"] is True
    assert created["stock_g"] == 500
    assert created["kcal"] is None
    assert "created_at" in created
    assert "updated_at" in created

    # 3. Get single product
    get_resp = await client.get(f"/nutrition/products/{product_id}")
    assert get_resp.status_code == 200
    assert get_resp.json() == created

    # 4. List products
    list_resp = await client.get("/nutrition/products")
    assert list_resp.status_code == 200
    items = list_resp.json()
    assert len(items) == 1
    assert items[0]["id"] == product_id

    # 5. Patch product (e.g. availability changed to low, preference to neutral, stock changed)
    patch_payload = {
        "availability": "low",
        "preference": "neutral",
        "stock_g": 400,
    }
    patch_resp = await client.patch(f"/nutrition/products/{product_id}", json=patch_payload)
    assert patch_resp.status_code == 200
    patched = patch_resp.json()
    assert patched["availability"] == "low"
    assert patched["preference"] == "neutral"
    assert patched["stock_g"] == 400
    assert patched["name"] == "Волоські горіхи"

    # 6. Delete product
    del_resp = await client.delete(f"/nutrition/products/{product_id}")
    assert del_resp.status_code == 204

    # 7. Verify deleted
    get_after_del = await client.get(f"/nutrition/products/{product_id}")
    assert get_after_del.status_code == 404


@pytest.mark.asyncio
async def test_product_not_found_returns_404(client: AsyncClient):
    random_id = uuid4()
    assert (await client.get(f"/nutrition/products/{random_id}")).status_code == 404
    patch_resp = await client.patch(f"/nutrition/products/{random_id}", json={"name": "X"})
    assert patch_resp.status_code == 404
    assert (await client.delete(f"/nutrition/products/{random_id}")).status_code == 404


@pytest.mark.asyncio
async def test_product_user_isolation(client_factory, make_user):
    user_a = await make_user("a@example.com")
    user_b = await make_user("b@example.com")

    client_a = client_factory(user_a)
    created = await client_a.post("/nutrition/products", json={"name": "Яйця", "count": 10})
    assert created.status_code == 201
    prod_id = created.json()["id"]

    client_b = client_factory(user_b)
    # User B cannot see User A's products in list
    list_b = await client_b.get("/nutrition/products")
    assert list_b.status_code == 200
    assert list_b.json() == []

    # User B cannot get/patch/delete User A's product
    assert (await client_b.get(f"/nutrition/products/{prod_id}")).status_code == 404
    patch_b = await client_b.patch(f"/nutrition/products/{prod_id}", json={"count": 5})
    assert patch_b.status_code == 404
    assert (await client_b.delete(f"/nutrition/products/{prod_id}")).status_code == 404


@pytest.mark.asyncio
async def test_product_estimation_on_create_and_name_change(client: AsyncClient):
    walnut_nutrition = {
        "kcal": 654.0,
        "protein_g": 15.2,
        "fat_g": 65.2,
        "carbs_g": 7.0,
        "fiber_g": 6.7,
        "sugar_g": 2.6,
        "sodium_mg": 2.0,
    }
    hazelnut_nutrition = {
        "kcal": 628.0,
        "protein_g": 15.0,
        "fat_g": 60.8,
        "carbs_g": 16.7,
        "fiber_g": 9.7,
        "sugar_g": 4.3,
        "sodium_mg": 0.0,
    }

    # 1. Create with mocked Gemini
    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_gemini:
        mock_gemini.return_value = GeminiResult(
            data=walnut_nutrition,
            raw_text='{"kcal": 654}',
            model="gemini-2.0-flash",
            input_tokens=100,
            output_tokens=50,
        )
        resp = await client.post(
            "/nutrition/products",
            json={"name": "Волоські горіхи", "stock_g": 300},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["kcal"] == 654.0
        assert data["protein_g"] == 15.2
        assert data["fat_g"] == 65.2
        assert data["carbs_g"] == 7.0
        assert data["fiber_g"] == 6.7
        assert data["sugar_g"] == 2.6
        assert data["sodium_mg"] == 2.0
        prod_id = data["id"]
        assert mock_gemini.call_count == 1

    # 2. Patch without name change: Gemini is not called
    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_gemini:
        patch_resp = await client.patch(
            f"/nutrition/products/{prod_id}",
            json={"stock_g": 250},
        )
        assert patch_resp.status_code == 200
        assert patch_resp.json()["stock_g"] == 250
        assert patch_resp.json()["kcal"] == 654.0
        assert mock_gemini.call_count == 0

    # 3. Patch with name change: Gemini is called and updates values
    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_gemini:
        mock_gemini.return_value = GeminiResult(
            data=hazelnut_nutrition,
            raw_text='{"kcal": 628}',
            model="gemini-2.0-flash",
            input_tokens=100,
            output_tokens=50,
        )
        patch_resp = await client.patch(
            f"/nutrition/products/{prod_id}",
            json={"name": "Фундук"},
        )
        assert patch_resp.status_code == 200
        data = patch_resp.json()
        assert data["name"] == "Фундук"
        assert data["kcal"] == 628.0
        assert data["carbs_g"] == 16.7
        assert mock_gemini.call_count == 1

    # 4. Patch with name change when Gemini fails: keeps NULLs and returns product
    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_gemini:
        mock_gemini.side_effect = GeminiUnavailable("Service down")
        patch_resp = await client.patch(
            f"/nutrition/products/{prod_id}",
            json={"name": "Мигдаль"},
        )
        assert patch_resp.status_code == 200
        data = patch_resp.json()
        assert data["name"] == "Мигдаль"
        assert data["kcal"] is None
        assert data["protein_g"] is None


@pytest.mark.asyncio
async def test_product_estimate_retry_endpoint(client: AsyncClient):
    # Create product without Gemini (fails silently -> keeps NULLs)
    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_gemini:
        mock_gemini.side_effect = GeminiUnavailable("Timeout")
        resp = await client.post(
            "/nutrition/products",
            json={"name": "Кеш'ю", "stock_g": 200},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["kcal"] is None
        prod_id = data["id"]

    # Retry endpoint happy path
    cashew_nutrition = {
        "kcal": 553.0,
        "protein_g": 18.2,
        "fat_g": 43.8,
        "carbs_g": 30.2,
        "fiber_g": 3.3,
        "sugar_g": 5.9,
        "sodium_mg": 12.0,
    }
    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_gemini:
        mock_gemini.return_value = GeminiResult(
            data=cashew_nutrition,
            raw_text="{}",
            model="gemini-2.0-flash",
            input_tokens=100,
            output_tokens=50,
        )
        est_resp = await client.post(f"/nutrition/products/{prod_id}/estimate")
        assert est_resp.status_code == 200
        data = est_resp.json()
        assert data["kcal"] == 553.0
        assert data["protein_g"] == 18.2

    # Retry endpoint 404 for unknown product
    assert (await client.post(f"/nutrition/products/{uuid4()}/estimate")).status_code == 404

    # Retry endpoint 502 when Gemini unavailable
    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_gemini:
        mock_gemini.side_effect = GeminiUnavailable("Network error")
        err_resp = await client.post(f"/nutrition/products/{prod_id}/estimate")
        assert err_resp.status_code == 502


@pytest.mark.asyncio
async def test_product_untracked_stock(client: AsyncClient):
    resp = await client.post(
        "/nutrition/products",
        json={"name": "Сіль кухонна", "stock_g": None},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["stock_g"] is None
