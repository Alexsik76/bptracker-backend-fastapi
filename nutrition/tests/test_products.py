from uuid import uuid4

import pytest
from httpx import AsyncClient


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
        "daily_limit_g": 30,
    }
    create_resp = await client.post("/nutrition/products", json=payload)
    assert create_resp.status_code == 201
    created = create_resp.json()
    product_id = created["id"]
    assert created["name"] == "Волоські горіхи"
    assert created["availability"] == "in_stock"
    assert created["preference"] == "preferred"
    assert created["eat_raw"] is True
    assert created["daily_limit_g"] == 30
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

    # 5. Patch product (e.g. availability changed to low, preference to neutral)
    patch_payload = {
        "availability": "low",
        "preference": "neutral",
        "daily_limit_g": 20,
    }
    patch_resp = await client.patch(f"/nutrition/products/{product_id}", json=patch_payload)
    assert patch_resp.status_code == 200
    patched = patch_resp.json()
    assert patched["availability"] == "low"
    assert patched["preference"] == "neutral"
    assert patched["daily_limit_g"] == 20
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
