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
async def test_recipe_name_suffix_on_duplicate(client: AsyncClient):
    p = (await client.post("/nutrition/products", json={"name": "Буряк"})).json()

    # 1. First recipe "Борщ"
    r1 = (
        await client.post(
            "/nutrition/recipes",
            json={"name": "Борщ", "product_ids": [p["id"]]},
        )
    ).json()
    assert r1["name"] == "Борщ"

    # 2. Duplicate create -> auto-suffix "Борщ 2"
    r2 = (
        await client.post(
            "/nutrition/recipes",
            json={"name": "Борщ", "product_ids": [p["id"]]},
        )
    ).json()
    assert r2["name"] == "Борщ 2"

    # 3. Third duplicate create -> auto-suffix "Борщ 3"
    r3 = (
        await client.post(
            "/nutrition/recipes",
            json={"name": "Борщ", "product_ids": [p["id"]]},
        )
    ).json()
    assert r3["name"] == "Борщ 3"


@pytest.mark.asyncio
async def test_recipe_archive_filter(client: AsyncClient):
    p = (await client.post("/nutrition/products", json={"name": "Морква"})).json()

    # Create active recipe
    await client.post(
        "/nutrition/recipes",
        json={"name": "Морквяний салат", "product_ids": [p["id"]]},
    )

    # Create another recipe and archive it
    r_arch = (
        await client.post(
            "/nutrition/recipes",
            json={"name": "Морквяний сік", "product_ids": [p["id"]]},
        )
    ).json()
    patch_resp = await client.patch(
        f"/nutrition/recipes/{r_arch['id']}",
        json={"archived": True},
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["archived"] is True

    # 1. GET list without include_archived hides archived recipe
    list_resp = await client.get("/nutrition/recipes")
    assert list_resp.status_code == 200
    names = [r["name"] for r in list_resp.json()]
    assert "Морквяний салат" in names
    assert "Морквяний сік" not in names

    # 2. GET list with ?include_archived=true includes both
    list_all = await client.get("/nutrition/recipes?include_archived=true")
    assert list_all.status_code == 200
    all_names = [r["name"] for r in list_all.json()]
    assert "Морквяний салат" in all_names
    assert "Морквяний сік" in all_names


@pytest.mark.asyncio
async def test_recipe_delete_409_when_referenced_by_dish_or_frequent_dish(client: AsyncClient):
    p = (await client.post("/nutrition/products", json={"name": "Гречка", "stock_g": 500})).json()

    # Recipe 1 referenced by a dish
    r1 = (
        await client.post(
            "/nutrition/recipes",
            json={"name": "Гречана каша з маслом", "product_ids": [p["id"]]},
        )
    ).json()
    dish = (
        await client.post(
            "/nutrition/dishes",
            json={
                "recipe_id": r1["id"],
                "cooked_weight_g": 300,
                "components": [{"product_id": p["id"], "weight_g": 100}],
            },
        )
    ).json()

    # Attempting to delete r1 returns 409
    del1 = await client.delete(f"/nutrition/recipes/{r1['id']}")
    assert del1.status_code == 409
    assert "referenced" in del1.json()["detail"].lower()

    # Recipe 2 referenced by a frequent dish
    r2 = (
        await client.post(
            "/nutrition/recipes",
            json={"name": "Гречка фірмова", "product_ids": [p["id"]]},
        )
    ).json()
    fd = (
        await client.post(
            "/nutrition/frequent-dishes",
            json={
                "recipe_id": r2["id"],
                "cooked_weight_g": 250,
                "components": [{"product_id": p["id"], "weight_g": 80}],
            },
        )
    ).json()

    # Attempting to delete r2 returns 409
    del2 = await client.delete(f"/nutrition/recipes/{r2['id']}")
    assert del2.status_code == 409
    assert "referenced" in del2.json()["detail"].lower()

    # Delete dish and frequent dish, now deleting recipes succeeds
    await client.delete(f"/nutrition/dishes/{dish['id']}")
    del1_ok = await client.delete(f"/nutrition/recipes/{r1['id']}")
    assert del1_ok.status_code == 204

    await client.delete(f"/nutrition/frequent-dishes/{fd['id']}")
    del2_ok = await client.delete(f"/nutrition/recipes/{r2['id']}")
    assert del2_ok.status_code == 204


@pytest.mark.asyncio
async def test_recipe_user_isolation(client_factory, make_user):
    user_a = await make_user("user_a_recipe@example.com")
    user_b = await make_user("user_b_recipe@example.com")

    client_a = client_factory(user_a)
    p_a = (await client_a.post("/nutrition/products", json={"name": "Капуста"})).json()
    r_a = (
        await client_a.post(
            "/nutrition/recipes",
            json={"name": "Тушкована капуста", "product_ids": [p_a["id"]]},
        )
    ).json()

    client_b = client_factory(user_b)
    # User B cannot see or modify User A's recipe
    assert (await client_b.get(f"/nutrition/recipes/{r_a['id']}")).status_code == 404
    assert (
        await client_b.patch(
            f"/nutrition/recipes/{r_a['id']}", json={"name": "Хак"}
        )
    ).status_code == 404
    assert (await client_b.delete(f"/nutrition/recipes/{r_a['id']}")).status_code == 404
