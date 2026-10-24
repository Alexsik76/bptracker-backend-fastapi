from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from httpx import AsyncClient

from gemini_infra.types import GeminiResult


@pytest.mark.asyncio
async def test_off_plan_crud(client: AsyncClient):
    # 1. Create two entries with different timestamps out of order
    at_late = datetime(2026, 10, 24, 15, 30, tzinfo=UTC).isoformat()
    at_early = datetime(2026, 10, 24, 10, 0, tzinfo=UTC).isoformat()

    resp1 = await client.post(
        "/nutrition/off-plan",
        json={
            "date": "2026-10-24",
            "at": at_late,
            "text": "Печиво вівсяне 2 шт.",
            "kcal_est": 180,
        },
    )
    assert resp1.status_code == 201
    entry1 = resp1.json()
    assert entry1["date"] == "2026-10-24"
    assert entry1["text"] == "Печиво вівсяне 2 шт."
    assert entry1["kcal_est"] == 180
    assert "id" in entry1
    assert "user_id" in entry1

    resp2 = await client.post(
        "/nutrition/off-plan",
        json={
            "date": "2026-10-24",
            "at": at_early,
            "text": "  Горіхи волоські 30 г  ",
            "kcal_est": None,
        },
    )
    assert resp2.status_code == 201
    entry2 = resp2.json()
    assert entry2["text"] == "Горіхи волоські 30 г"  # stripped
    assert entry2["kcal_est"] is None

    # 2. GET /nutrition/off-plan?date=YYYY-MM-DD should return sorted by 'at'
    list_resp = await client.get("/nutrition/off-plan?date=2026-10-24")
    assert list_resp.status_code == 200
    items = list_resp.json()
    assert len(items) == 2
    assert items[0]["id"] == entry2["id"]  # 10:00 first
    assert items[1]["id"] == entry1["id"]  # 15:30 second

    # 3. DELETE /nutrition/off-plan/{id}
    del_resp = await client.delete(f"/nutrition/off-plan/{entry1['id']}")
    assert del_resp.status_code == 204

    # Verify deleted
    list_resp2 = await client.get("/nutrition/off-plan?date=2026-10-24")
    assert list_resp2.status_code == 200
    items2 = list_resp2.json()
    assert len(items2) == 1
    assert items2[0]["id"] == entry2["id"]

    # 4. DELETE again returns 404
    del_resp2 = await client.delete(f"/nutrition/off-plan/{entry1['id']}")
    assert del_resp2.status_code == 404
    assert del_resp2.json()["detail"] == "Off-plan entry not found"


@pytest.mark.asyncio
async def test_off_plan_validation(client: AsyncClient):
    at_str = datetime(2026, 10, 24, 12, 0, tzinfo=UTC).isoformat()

    # Empty text
    resp_empty = await client.post(
        "/nutrition/off-plan",
        json={
            "date": "2026-10-24",
            "at": at_str,
            "text": "   ",
        },
    )
    assert resp_empty.status_code == 400
    assert "cannot be empty" in resp_empty.json()["detail"]

    # Missing date
    resp_missing_date = await client.post(
        "/nutrition/off-plan",
        json={
            "at": at_str,
            "text": "Кава",
        },
    )
    assert resp_missing_date.status_code == 422


@pytest.mark.asyncio
async def test_off_plan_user_isolation(client_factory, make_user):
    user_a = await make_user("user_a@example.com")
    user_b = await make_user("user_b@example.com")

    client_a = client_factory(user_a)

    at_str = datetime(2026, 10, 24, 12, 0, tzinfo=UTC).isoformat()
    resp_a = await client_a.post(
        "/nutrition/off-plan",
        json={
            "date": "2026-10-24",
            "at": at_str,
            "text": "Шоколад чорний 20 г",
            "kcal_est": 110,
        },
    )
    assert resp_a.status_code == 201
    entry_a_id = resp_a.json()["id"]

    client_b = client_factory(user_b)

    # User B cannot see User A's off-plan entries
    resp_b_list = await client_b.get("/nutrition/off-plan?date=2026-10-24")
    assert resp_b_list.status_code == 200
    assert resp_b_list.json() == []

    # User B cannot see User A's entry in DayMenu
    resp_b_menu = await client_b.get("/nutrition/menus/2026-10-24")
    assert resp_b_menu.status_code == 404

    # User B cannot delete User A's off-plan entry
    resp_b_del = await client_b.delete(f"/nutrition/off-plan/{entry_a_id}")
    assert resp_b_del.status_code == 404


@pytest.mark.asyncio
async def test_day_menu_contains_notes_and_off_plan(client: AsyncClient):
    # Setup profile
    await client.put(
        "/nutrition/profile",
        json={
            "sex": "male",
            "birth_year": 1976,
            "height_cm": 200,
            "weight_kg": 118,
            "target_weight_kg": 110,
            "pace_kg_per_week": 0.5,
            "restrictions": "Без жовчного",
            "dislikes": "Печінка",
            "meals": [
                {"name": "Сніданок", "time": "08:00"},
                {"name": "Обід", "time": "13:00"},
            ],
        },
    )

    # Add product and menu item
    prod_resp = await client.post(
        "/nutrition/products",
        json={"name": "Сир кисломолочний 5%"},
    )
    prod_id = prod_resp.json()["id"]

    await client.post(
        "/nutrition/menus/2026-10-24/items?slot=Сніданок",
        json={"product_id": prod_id, "portion_g": 150},
    )

    # Add note
    mock_result = GeminiResult(
        data={"answer": "150 г сиру — відмінно."},
        raw_text='{"answer": "150 г сиру — відмінно."}',
        model="gemini-2.0-flash",
        input_tokens=100,
        output_tokens=20,
    )
    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_gen:
        mock_gen.return_value = mock_result
        note_resp = await client.post(
            "/nutrition/menus/2026-10-24/notes",
            json={"kind": "info", "text": "чи можна сир на сніданок?"},
        )
        assert note_resp.status_code == 201

    # Add off-plan entry
    at_str = datetime(2026, 10, 24, 11, 0, tzinfo=UTC).isoformat()
    off_resp = await client.post(
        "/nutrition/off-plan",
        json={
            "date": "2026-10-24",
            "at": at_str,
            "text": "Перекус яблуком",
            "kcal_est": 60,
        },
    )
    assert off_resp.status_code == 201

    # GET /nutrition/menus/{date}
    day_resp = await client.get("/nutrition/menus/2026-10-24")
    assert day_resp.status_code == 200
    day_data = day_resp.json()
    assert day_data["date"] == "2026-10-24"

    assert len(day_data["menus"]) == 1
    assert day_data["menus"][0]["slot"] == "Сніданок"

    assert len(day_data["notes"]) == 1
    assert day_data["notes"][0]["text"] == "чи можна сир на сніданок?"
    assert day_data["notes"][0]["answer"] == "150 г сиру — відмінно."

    assert len(day_data["off_plan"]) == 1
    assert day_data["off_plan"][0]["text"] == "Перекус яблуком"


@pytest.mark.asyncio
async def test_day_menu_404_rule(client: AsyncClient):
    date_str = "2026-10-25"

    # 1. Day with nothing -> 404
    resp = await client.get(f"/nutrition/menus/{date_str}")
    assert resp.status_code == 404

    # 2. Add only off-plan entry -> 200 with empty menus and notes
    at_str = datetime(2026, 10, 25, 9, 0, tzinfo=UTC).isoformat()
    off_resp = await client.post(
        "/nutrition/off-plan",
        json={
            "date": date_str,
            "at": at_str,
            "text": "Банан",
        },
    )
    assert off_resp.status_code == 201
    entry_id = off_resp.json()["id"]

    resp2 = await client.get(f"/nutrition/menus/{date_str}")
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["menus"] == []
    assert data2["notes"] == []
    assert len(data2["off_plan"]) == 1
    assert data2["off_plan"][0]["text"] == "Банан"

    # 3. Delete off-plan entry -> 404 again
    del_resp = await client.delete(f"/nutrition/off-plan/{entry_id}")
    assert del_resp.status_code == 204

    resp3 = await client.get(f"/nutrition/menus/{date_str}")
    assert resp3.status_code == 404

    # 4. Non-existent id deletion -> 404
    fake_id = uuid4()
    del_fake = await client.delete(f"/nutrition/off-plan/{fake_id}")
    assert del_fake.status_code == 404

    # 5. Add only info note (no menu, no off-plan) -> 200
    await client.put(
        "/nutrition/profile",
        json={
            "sex": "male",
            "birth_year": 1976,
            "height_cm": 200,
            "weight_kg": 118,
            "target_weight_kg": 110,
            "pace_kg_per_week": 0.5,
            "meals": [{"name": "Сніданок", "time": "08:00"}],
        },
    )
    mock_result = GeminiResult(
        data={"answer": "Так, можна."},
        raw_text='{"answer": "Так, можна."}',
        model="gemini-2.0-flash",
        input_tokens=50,
        output_tokens=10,
    )
    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_gen:
        mock_gen.return_value = mock_result
        note_resp = await client.post(
            f"/nutrition/menus/{date_str}/notes",
            json={"kind": "info", "text": "питання без меню"},
        )
        assert note_resp.status_code == 201

    resp4 = await client.get(f"/nutrition/menus/{date_str}")
    assert resp4.status_code == 200
    data4 = resp4.json()
    assert data4["menus"] == []
    assert data4["off_plan"] == []
    assert len(data4["notes"]) == 1
    assert data4["notes"][0]["text"] == "питання без меню"

