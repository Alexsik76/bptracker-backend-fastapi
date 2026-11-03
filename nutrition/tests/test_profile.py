import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_get_profile_not_found(client: AsyncClient):
    response = await client.get("/nutrition/profile")
    assert response.status_code == 404
    assert response.json()["detail"] == "Nutrition profile not found"


@pytest.mark.asyncio
async def test_upsert_and_get_profile(client: AsyncClient):
    payload = {
        "sex": "male",
        "birth_year": 1976,
        "height_cm": 200,
        "weight_kg": 118.0,
        "target_weight_kg": 110.0,
        "pace_kg_per_week": 0.75,
        "restrictions": "Обмеження солі",
        "dislikes": "Печінка",
        "weight_variety": 0.6,
        "weight_balance": 0.7,
        "weight_goal": 0.8,
    }

    put_resp = await client.put("/nutrition/profile", json=payload)
    assert put_resp.status_code == 200
    data = put_resp.json()
    assert data["sex"] == "male"
    assert data["birth_year"] == 1976
    assert data["height_cm"] == 200
    assert data["weight_kg"] == 118.0
    assert data["target_weight_kg"] == 110.0
    assert data["weight_variety"] == 0.6
    assert data["weight_balance"] == 0.7
    assert data["weight_goal"] == 0.8
    assert "updated_at" in data
    assert "user_id" in data

    get_resp = await client.get("/nutrition/profile")
    assert get_resp.status_code == 200
    assert get_resp.json() == data

    # Update (complete replacement) with default weights
    updated_payload = {
        "sex": "male",
        "birth_year": 1976,
        "height_cm": 200,
        "weight_kg": 115.0,
        "target_weight_kg": 110.0,
        "pace_kg_per_week": 0.5,
    }
    put_resp2 = await client.put("/nutrition/profile", json=updated_payload)
    assert put_resp2.status_code == 200
    data2 = put_resp2.json()
    assert data2["weight_kg"] == 115.0
    assert data2["restrictions"] is None
    assert data2["weight_variety"] == 0.5
    assert data2["weight_balance"] == 0.5
    assert data2["weight_goal"] == 0.5


@pytest.mark.asyncio
async def test_profile_validates_weights(client: AsyncClient):
    base_payload = {
        "sex": "female",
        "birth_year": 1990,
        "height_cm": 165,
        "weight_kg": 60.0,
        "target_weight_kg": 55.0,
        "pace_kg_per_week": 0.5,
    }

    # Negative weight
    resp_neg = await client.put(
        "/nutrition/profile",
        json={**base_payload, "weight_variety": -0.1},
    )
    assert resp_neg.status_code == 422

    # Weight > 1.0
    resp_over = await client.put(
        "/nutrition/profile",
        json={**base_payload, "weight_balance": 1.2},
    )
    assert resp_over.status_code == 422


@pytest.mark.asyncio
async def test_profile_user_isolation(client_factory, make_user):
    user_a = await make_user("pa@example.com")
    user_b = await make_user("pb@example.com")

    client_a = client_factory(user_a)

    payload_a = {
        "sex": "female",
        "birth_year": 1992,
        "height_cm": 170,
        "weight_kg": 65.0,
        "target_weight_kg": 60.0,
        "pace_kg_per_week": 0.5,
    }
    await client_a.put("/nutrition/profile", json=payload_a)

    client_b = client_factory(user_b)
    # User B should see 404
    get_b = await client_b.get("/nutrition/profile")
    assert get_b.status_code == 404
