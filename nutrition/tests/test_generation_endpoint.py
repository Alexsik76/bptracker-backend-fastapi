from datetime import date
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from gemini_infra.types import GeminiInvalidResponse, GeminiResult, GeminiUnavailable
from nutrition.models import GenerationLog


async def setup_nutrition_data(client: AsyncClient):
    # 1. Setup profile
    await client.put(
        "/nutrition/profile",
        json={
            "sex": "male",
            "birth_year": 1976,
            "height_cm": 200,
            "weight_kg": 118,
            "target_weight_kg": 110,
            "pace_kg_per_week": 0.5,
            "restrictions": "Без жовчного міхура",
            "dislikes": "Печінка",
            "meals": [
                {"name": "Сніданок", "time": "08:00"},
                {"name": "Обід", "time": "13:00"},
                {"name": "Вечеря", "time": "19:00"},
            ],
        },
    )

    # 2. Setup ready dish
    dish_resp = await client.post(
        "/nutrition/dishes",
        json={
            "name": "Індичка запечена",
            "kind": "ready",
            "cooked_weight_g": 600,
            "cooked_on": "2026-10-23",
        },
    )
    dish_id = dish_resp.json()["id"]

    # 3. Setup product
    prod_resp = await client.post(
        "/nutrition/products",
        json={
            "name": "Яблуко",
            "category": "Фрукти",
            "daily_limit_g": 300,
        },
    )
    prod_id = prod_resp.json()["id"]

    return {"dish_id": dish_id, "prod_id": prod_id}


@pytest.mark.asyncio
async def test_generate_menu_success(client: AsyncClient, session: AsyncSession):
    nutrition_data = await setup_nutrition_data(client)
    dish_id = nutrition_data["dish_id"]
    prod_id = nutrition_data["prod_id"]

    mock_gemini_data = {
        "rationale": "Збалансований раціон із білком",
        "shopping_list": ["Огірки", "Зелень"],
        "items": [
            {
                "slot": "Обід",
                "dish_id": dish_id,
                "product_id": None,
                "title": "Індичка запечена",
                "portion_g": 200,
                "portion_text": "200 г",
                "kcal_est": 260,
                "position": 1,
            },
            {
                "slot": "Сніданок",
                "dish_id": None,
                "product_id": prod_id,
                "title": "Яблуко",
                "portion_g": 150,
                "portion_text": "1 шт",
                "kcal_est": 75,
                "position": 1,
            },
        ],
    }

    mock_result = GeminiResult(
        data=mock_gemini_data,
        raw_text='{"rationale": "Збалансований раціон із білком"}',
        model="gemini-2.0-flash",
        input_tokens=1500,
        output_tokens=300,
    )

    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_generate:
        mock_generate.return_value = mock_result

        resp = await client.post(
            "/nutrition/menus/2026-10-23/generate",
            json={"scope": "day"},
        )
        assert resp.status_code == 200
        data = resp.json()

        assert data["rationale"] == "Збалансований раціон із білком"
        assert data["shopping_list"] == ["Огірки", "Зелень"]
        menus = data["menus"]
        assert len(menus) == 2
        slots = {m["slot"] for m in menus}
        assert slots == {"Обід", "Сніданок"}
        for m in menus:
            assert m["status"] == "planned"
            assert m["regenerations"] == 1

        # Verify generation_log entry was created
        stmt = select(GenerationLog).where(GenerationLog.date == date(2026, 10, 23))
        logs = (await session.exec(stmt)).all()
        assert len(logs) == 1
        assert logs[0].scope == "day"
        assert logs[0].input_tokens == 1500
        assert logs[0].output_tokens == 300


@pytest.mark.asyncio
async def test_generate_keeps_eaten_and_skipped_meals(client: AsyncClient):
    nutrition_data = await setup_nutrition_data(client)
    dish_id = nutrition_data["dish_id"]
    prod_id = nutrition_data["prod_id"]

    # Add Breakfast manual item and mark eaten
    item1_resp = await client.post(
        "/nutrition/menus/2026-10-23/items?slot=Сніданок",
        json={
            "product_id": prod_id,
            "portion_g": 100,
        },
    )
    menu_id = item1_resp.json()["menu_id"]
    await client.patch(f"/nutrition/menus/{menu_id}", json={"status": "eaten"})

    # Add Lunch manual item (planned)
    item2_resp = await client.post(
        "/nutrition/menus/2026-10-23/items?slot=Обід",
        json={
            "dish_id": dish_id,
            "portion_g": 200,
        },
    )
    item2_id = item2_resp.json()["id"]

    # Mock generator returning new item for Lunch and Dinner
    mock_result = GeminiResult(
        data={
            "rationale": "Оновлений обід та вечеря",
            "shopping_list": [],
            "items": [
                {
                    "slot": "Обід",
                    "dish_id": dish_id,
                    "product_id": None,
                    "title": "Індичка запечена",
                    "portion_g": 220,
                    "portion_text": "220 г",
                    "kcal_est": 290,
                    "position": 1,
                },
                {
                    "slot": "Вечеря",
                    "dish_id": dish_id,
                    "product_id": None,
                    "title": "Індичка запечена",
                    "portion_g": 180,
                    "portion_text": "180 г",
                    "kcal_est": 240,
                    "position": 1,
                },
            ],
        },
        raw_text="{}",
        model="gemini-2.0-flash",
        input_tokens=100,
        output_tokens=50,
    )

    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_generate:
        mock_generate.return_value = mock_result

        resp = await client.post(
            "/nutrition/menus/2026-10-23/generate",
            json={"scope": "day"},
        )
        assert resp.status_code == 200
        menus = resp.json()["menus"]
        menu_by_slot = {m["slot"]: m for m in menus}

        # Breakfast (eaten) must still be eaten and intact!
        assert menu_by_slot["Сніданок"]["status"] == "eaten"
        assert menu_by_slot["Сніданок"]["items"][0]["portion_g"] == 100

        # Lunch (planned) has been replaced by generated item
        assert menu_by_slot["Обід"]["items"][0]["id"] != item2_id
        assert menu_by_slot["Обід"]["items"][0]["portion_g"] == 220

        # Dinner is present
        assert "Вечеря" in menu_by_slot


@pytest.mark.asyncio
async def test_generate_slot_scope(client: AsyncClient):
    nutrition_data = await setup_nutrition_data(client)
    dish_id = nutrition_data["dish_id"]

    mock_result = GeminiResult(
        data={
            "rationale": "Лише обід",
            "shopping_list": [],
            "items": [
                {
                    "slot": "Обід",
                    "dish_id": dish_id,
                    "product_id": None,
                    "title": "Індичка запечена",
                    "portion_g": 200,
                    "portion_text": "200 г",
                    "kcal_est": 260,
                    "position": 1,
                },
                {
                    "slot": "Вечеря",
                    "dish_id": dish_id,
                    "product_id": None,
                    "title": "Ігнорована вечеря",
                    "portion_g": 200,
                    "portion_text": "200 г",
                    "kcal_est": 260,
                    "position": 1,
                },
            ],
        },
        raw_text="{}",
        model="gemini-2.0-flash",
        input_tokens=100,
        output_tokens=50,
    )

    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_generate:
        mock_generate.return_value = mock_result

        resp = await client.post(
            "/nutrition/menus/2026-10-23/generate",
            json={"scope": "slot", "slot": "Обід"},
        )
        assert resp.status_code == 200
        menus = resp.json()["menus"]
        slots = [m["slot"] for m in menus]
        assert "Обід" in slots
        assert "Вечеря" not in slots


@pytest.mark.asyncio
async def test_generate_missing_profile_returns_409(client: AsyncClient):
    # Without setting up profile
    resp = await client.post(
        "/nutrition/menus/2026-10-23/generate",
        json={"scope": "day"},
    )
    assert resp.status_code == 409
    assert "Nutrition profile not found" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_generate_gemini_unavailable_502(client: AsyncClient):
    await setup_nutrition_data(client)
    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_generate:
        mock_generate.side_effect = GeminiUnavailable()

        resp = await client.post(
            "/nutrition/menus/2026-10-23/generate",
            json={"scope": "day"},
        )
        assert resp.status_code == 502
        assert resp.json()["detail"] == "Gemini service is unavailable"


@pytest.mark.asyncio
async def test_generate_invalid_response_422_and_logged(client: AsyncClient, session: AsyncSession):
    await setup_nutrition_data(client)
    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_generate:
        mock_generate.side_effect = GeminiInvalidResponse(
            "Syntax error", raw_text='{"malformed": true'
        )

        resp = await client.post(
            "/nutrition/menus/2026-10-23/generate",
            json={"scope": "day"},
        )
        assert resp.status_code == 422
        assert "Invalid Gemini response" in resp.json()["detail"]

        stmt = select(GenerationLog).where(GenerationLog.raw_response == '{"malformed": true')
        log = (await session.exec(stmt)).first()
        assert log is not None
        assert log.scope == "day"
