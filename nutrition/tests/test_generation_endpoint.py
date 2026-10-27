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
        assert len(menus) == 3
        slots = [m["slot"] for m in menus]
        assert slots == ["Сніданок", "Обід", "Вечеря"]
        breakfast = [m for m in menus if m["slot"] == "Сніданок"][0]
        lunch = [m for m in menus if m["slot"] == "Обід"][0]
        dinner = [m for m in menus if m["slot"] == "Вечеря"][0]
        assert breakfast["status"] == "planned"
        assert breakfast["regenerations"] == 1
        assert breakfast["id"] is not None
        assert lunch["status"] == "planned"
        assert lunch["regenerations"] == 1
        assert lunch["id"] is not None
        assert dinner["status"] == "planned"
        assert dinner["regenerations"] == 0
        assert dinner["id"] is None
        assert dinner["items"] == []

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
            "kcal_est": 50,
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
            "kcal_est": 250,
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
        lunch = [m for m in menus if m["slot"] == "Обід"][0]
        assert lunch["items"] != []
        dinner = [m for m in menus if m["slot"] == "Вечеря"][0]
        assert dinner["items"] == []
        assert dinner["id"] is None


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


@pytest.mark.asyncio
async def test_slot_generation_keeps_other_meals_untouched_and_passes_context(client: AsyncClient):
    data = await setup_nutrition_data(client)
    dish_id = data["dish_id"]
    prod_id = data["prod_id"]

    # Breakfast: manual item with 150 kcal
    b_resp = await client.post(
        "/nutrition/menus/2026-10-23/items?slot=Сніданок",
        json={"product_id": prod_id, "portion_g": 100, "kcal_est": 150},
    )
    b_id = b_resp.json()["id"]

    # Dinner: manual item with 200 kcal
    d_resp = await client.post(
        "/nutrition/menus/2026-10-23/items?slot=Вечеря",
        json={"title": "Салат", "portion_g": 150, "kcal_est": 200},
    )
    d_id = d_resp.json()["id"]

    captured_prompt = None

    async def mock_gen(prompt, model, settings, response_schema):
        nonlocal captured_prompt
        captured_prompt = prompt
        return GeminiResult(
            data={
                "rationale": "Обід для слота",
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
                    }
                ],
            },
            raw_text="{}",
            model="gemini-2.0-flash",
            input_tokens=100,
            output_tokens=50,
        )

    with patch("gemini_infra.generate_json", side_effect=mock_gen):
        resp = await client.post(
            "/nutrition/menus/2026-10-23/generate",
            json={"scope": "slot", "slot": "Обід"},
        )
        assert resp.status_code == 200

    # Verify captured prompt passed other meals with kcal as fixed context
    assert "Сніданок" in captured_prompt
    assert "150" in captured_prompt
    assert "Вечеря" in captured_prompt
    assert "200" in captured_prompt
    assert "фіксований контекст" in captured_prompt

    # Verify other meals in DB are completely untouched
    day_resp = await client.get("/nutrition/menus/2026-10-23")
    menus = {m["slot"]: m for m in day_resp.json()["menus"]}
    assert len(menus["Сніданок"]["items"]) == 1
    assert menus["Сніданок"]["items"][0]["id"] == b_id
    assert len(menus["Вечеря"]["items"]) == 1
    assert menus["Вечеря"]["items"][0]["id"] == d_id
    assert len(menus["Обід"]["items"]) == 1
    assert menus["Обід"]["items"][0]["title"] == "Індичка запечена"


@pytest.mark.asyncio
async def test_day_generation_replaces_unmarked_manual_items_not_eaten_or_skipped(
    client: AsyncClient,
):
    data = await setup_nutrition_data(client)
    dish_id = data["dish_id"]
    prod_id = data["prod_id"]

    # Breakfast: item1 (eaten), item2 (unmarked manual)
    b1_resp = await client.post(
        "/nutrition/menus/2026-10-23/items?slot=Сніданок",
        json={"product_id": prod_id, "portion_g": 100, "kcal_est": 50},
    )
    b1_id = b1_resp.json()["id"]
    await client.patch(f"/nutrition/menu-items/{b1_id}", json={"status": "eaten"})

    b2_resp = await client.post(
        "/nutrition/menus/2026-10-23/items?slot=Сніданок",
        json={"title": "Бутерброд", "portion_g": 80, "kcal_est": 180},
    )
    b2_id = b2_resp.json()["id"]

    # Lunch: item3 (skipped), item4 (unmarked manual)
    l1_resp = await client.post(
        "/nutrition/menus/2026-10-23/items?slot=Обід",
        json={"dish_id": dish_id, "portion_g": 150, "kcal_est": 200},
    )
    l1_id = l1_resp.json()["id"]
    await client.patch(f"/nutrition/menu-items/{l1_id}", json={"status": "skipped"})

    l2_resp = await client.post(
        "/nutrition/menus/2026-10-23/items?slot=Обід",
        json={"title": "Суп", "portion_g": 250, "kcal_est": 120},
    )
    l2_id = l2_resp.json()["id"]

    captured_prompt = None

    async def mock_gen(prompt, model, settings, response_schema):
        nonlocal captured_prompt
        captured_prompt = prompt
        return GeminiResult(
            data={
                "rationale": "Оновлення раціону",
                "shopping_list": [],
                "items": [
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
                ],
            },
            raw_text="{}",
            model="gemini-2.0-flash",
            input_tokens=100,
            output_tokens=50,
        )

    with patch("gemini_infra.generate_json", side_effect=mock_gen):
        resp = await client.post(
            "/nutrition/menus/2026-10-23/generate",
            json={"scope": "day"},
        )
        assert resp.status_code == 200

    assert "skipped — do not propose again in this meal" in captured_prompt

    day_resp = await client.get("/nutrition/menus/2026-10-23")
    menus = {m["slot"]: m for m in day_resp.json()["menus"]}

    # Breakfast: eaten kept, unmarked manual replaced
    b_items = menus["Сніданок"]["items"]
    assert len(b_items) == 2
    assert b_items[0]["id"] == b1_id
    assert b_items[0]["status"] == "eaten"
    assert b_items[1]["id"] != b2_id
    assert b_items[1]["origin"] == "generated"
    assert b_items[1]["title"] == "Яблуко"

    # Lunch: skipped kept, unmarked manual replaced
    l_items = menus["Обід"]["items"]
    assert len(l_items) == 2
    assert l_items[0]["id"] == l1_id
    assert l_items[0]["status"] == "skipped"
    assert l_items[1]["id"] != l2_id
    assert l_items[1]["origin"] == "generated"
    assert l_items[1]["title"] == "Індичка запечена"


@pytest.mark.asyncio
async def test_removed_meals_never_recreated_by_generation(
    client: AsyncClient, session: AsyncSession
):
    data = await setup_nutrition_data(client)
    dish_id = data["dish_id"]

    # Delete Lunch meal slot
    del_resp = await client.delete("/nutrition/menus/2026-10-23/meals/Обід")
    assert del_resp.status_code == 204

    mock_result = GeminiResult(
        data={
            "rationale": "Тест видаленого",
            "shopping_list": [],
            "items": [
                {
                    "slot": "Сніданок",
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

    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_gen:
        mock_gen.return_value = mock_result
        resp = await client.post(
            "/nutrition/menus/2026-10-23/generate",
            json={"scope": "day"},
        )
        assert resp.status_code == 200
        menus = resp.json()["menus"]
        assert [m["slot"] for m in menus] == ["Сніданок", "Вечеря"]

    day_resp = await client.get("/nutrition/menus/2026-10-23")
    assert [m["slot"] for m in day_resp.json()["menus"]] == ["Сніданок", "Вечеря"]


@pytest.mark.asyncio
async def test_409_when_nothing_to_generate_no_gemini_call(client: AsyncClient):
    data = await setup_nutrition_data(client)
    prod_id = data["prod_id"]

    # Mark Breakfast, Lunch, Dinner as completed (eaten or skipped)
    for slot, st in [("Сніданок", "eaten"), ("Обід", "skipped"), ("Вечеря", "eaten")]:
        resp = await client.post(
            f"/nutrition/menus/2026-10-23/items?slot={slot}",
            json={"product_id": prod_id, "portion_g": 100, "kcal_est": 50},
        )
        item_id = resp.json()["id"]
        await client.patch(f"/nutrition/menu-items/{item_id}", json={"status": st})

    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_gen:
        # Day scope: 409
        resp_day = await client.post(
            "/nutrition/menus/2026-10-23/generate",
            json={"scope": "day"},
        )
        assert resp_day.status_code == 409
        assert "No generatable meals" in resp_day.json()["detail"]
        mock_gen.assert_not_called()

        # Slot scope: 409
        resp_slot = await client.post(
            "/nutrition/menus/2026-10-23/generate",
            json={"scope": "slot", "slot": "Сніданок"},
        )
        assert resp_slot.status_code == 409
        assert "Сніданок" in resp_slot.json()["detail"]
        mock_gen.assert_not_called()


@pytest.mark.asyncio
async def test_estimate_stage_runs_before_generation(client: AsyncClient):
    data = await setup_nutrition_data(client)
    dish_id = data["dish_id"]
    prod_id = data["prod_id"]

    # Add item to Breakfast without kcal_est
    b_resp = await client.post(
        "/nutrition/menus/2026-10-23/items?slot=Сніданок",
        json={"product_id": prod_id, "portion_g": 120},
    )
    b_id = b_resp.json()["id"]

    calls: list[str] = []

    async def mock_gemini(prompt, model, settings, response_schema):
        if "експертний нутриціолог" in prompt:
            calls.append("estimate")
            return GeminiResult(
                data={"items": [{"id": b_id, "kcal_est": 60}]},
                raw_text="{}",
                model="gemini-2.0-flash",
                input_tokens=50,
                output_tokens=20,
            )
        else:
            calls.append("generate")
            assert "60 (approximate estimate)" in prompt
            return GeminiResult(
                data={
                    "rationale": "Обід",
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
                        }
                    ],
                },
                raw_text="{}",
                model="gemini-2.0-flash",
                input_tokens=100,
                output_tokens=50,
            )

    with patch("gemini_infra.generate_json", side_effect=mock_gemini):
        resp = await client.post(
            "/nutrition/menus/2026-10-23/generate",
            json={"scope": "slot", "slot": "Обід"},
        )
        assert resp.status_code == 200
        assert calls == ["estimate", "generate"]

    # Verify Breakfast item kcal was updated with model source
    day_resp = await client.get("/nutrition/menus/2026-10-23")
    menus = {m["slot"]: m for m in day_resp.json()["menus"]}
    b_item = menus["Сніданок"]["items"][0]
    assert b_item["kcal_est"] == 60
    assert b_item["kcal_source"] == "model"


@pytest.mark.asyncio
async def test_day_menu_read_flags(client: AsyncClient):
    data = await setup_nutrition_data(client)
    prod_id = data["prod_id"]
    dish_id = data["dish_id"]

    # 1. Fresh day with projected meals (no items)
    r1 = (await client.get("/nutrition/menus/2026-10-23")).json()
    assert r1["can_generate_day"] is True
    assert r1["day_generation_replaces_manual"] is False

    # 2. Add an unmarked manual item
    i1 = (
        await client.post(
            "/nutrition/menus/2026-10-23/items?slot=Сніданок",
            json={"product_id": prod_id, "portion_g": 100, "kcal_est": 50},
        )
    ).json()
    r2 = (await client.get("/nutrition/menus/2026-10-23")).json()
    assert r2["can_generate_day"] is True
    assert r2["day_generation_replaces_manual"] is True

    # 3. Mark the manual item as eaten
    await client.patch(f"/nutrition/menu-items/{i1['id']}", json={"status": "eaten"})
    # Also add marked items to other slots so no meal has unmarked items
    i2 = (
        await client.post(
            "/nutrition/menus/2026-10-23/items?slot=Обід",
            json={"dish_id": dish_id, "portion_g": 200, "kcal_est": 250},
        )
    ).json()
    await client.patch(f"/nutrition/menu-items/{i2['id']}", json={"status": "skipped"})
    i3 = (
        await client.post(
            "/nutrition/menus/2026-10-23/items?slot=Вечеря",
            json={"product_id": prod_id, "portion_g": 150, "kcal_est": 75},
        )
    ).json()
    await client.patch(f"/nutrition/menu-items/{i3['id']}", json={"status": "eaten"})

    r3 = (await client.get("/nutrition/menus/2026-10-23")).json()
    assert r3["can_generate_day"] is False
    assert r3["day_generation_replaces_manual"] is False


@pytest.mark.asyncio
async def test_notes_flow_with_nothing_to_generate_does_not_fail(client: AsyncClient):
    data = await setup_nutrition_data(client)
    prod_id = data["prod_id"]

    # Mark all slots eaten/skipped
    for slot in ["Сніданок", "Обід", "Вечеря"]:
        resp = await client.post(
            f"/nutrition/menus/2026-10-23/items?slot={slot}",
            json={"product_id": prod_id, "portion_g": 100, "kcal_est": 50},
        )
        item_id = resp.json()["id"]
        await client.patch(f"/nutrition/menu-items/{item_id}", json={"status": "eaten"})

    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_gen:
        # Posting a correction note triggers generation of unmarked items.
        # Since nothing is generatable, it must treat it as "nothing to do" and succeed.
        resp = await client.post(
            "/nutrition/menus/2026-10-23/notes",
            json={"kind": "correction", "text": "Більше нічого не потрібно"},
        )
        assert resp.status_code == 201
        assert resp.json()["text"] == "Більше нічого не потрібно"
        mock_gen.assert_not_called()
