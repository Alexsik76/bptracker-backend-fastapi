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
        "meals": [
            {"name": "Сніданок", "time": "08:00"},
            {"name": "Обід", "time": "13:00"},
            {"name": "Вечеря", "time": "19:00"},
        ],
    }

    put_resp = await client.put("/nutrition/profile", json=payload)
    assert put_resp.status_code == 200
    data = put_resp.json()
    assert data["sex"] == "male"
    assert data["birth_year"] == 1976
    assert data["height_cm"] == 200
    assert data["weight_kg"] == 118.0
    assert data["target_weight_kg"] == 110.0
    assert len(data["meals"]) == 3
    assert data["meals"][0]["name"] == "Сніданок"
    assert "updated_at" in data
    assert "user_id" in data

    get_resp = await client.get("/nutrition/profile")
    assert get_resp.status_code == 200
    get_data = get_resp.json()
    assert get_data == data

    # Update (complete replacement)
    updated_payload = {
        "sex": "male",
        "birth_year": 1976,
        "height_cm": 200,
        "weight_kg": 115.0,
        "target_weight_kg": 110.0,
        "pace_kg_per_week": 0.5,
        "restrictions": None,
        "dislikes": None,
        "meals": [
            {"name": "Сніданок", "time": "09:00"},
            {"name": "Вечеря", "time": "18:00"},
        ],
    }
    put_resp2 = await client.put("/nutrition/profile", json=updated_payload)
    assert put_resp2.status_code == 200
    data2 = put_resp2.json()
    assert data2["weight_kg"] == 115.0
    assert data2["restrictions"] is None
    assert len(data2["meals"]) == 2
    assert data2["meals"][0]["time"] == "09:00"


@pytest.mark.asyncio
async def test_profile_user_isolation(client_factory, make_user):
    user_a = await make_user("user_a@example.com")
    user_b = await make_user("user_b@example.com")

    client_a = client_factory(user_a)
    payload = {
        "sex": "female",
        "birth_year": 1985,
        "height_cm": 170,
        "weight_kg": 65.0,
        "target_weight_kg": 60.0,
        "pace_kg_per_week": 0.5,
        "meals": [],
    }

    resp = await client_a.put("/nutrition/profile", json=payload)
    assert resp.status_code == 200

    # User B cannot see User A's profile
    client_b = client_factory(user_b)
    resp_b = await client_b.get("/nutrition/profile")
    assert resp_b.status_code == 404
