from datetime import date
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from gemini_infra import GeminiInvalidResponse, GeminiResult, GeminiUnavailable
from nutrition.models import GenerationLog


async def setup_nutrition_data(client: AsyncClient):
    # 1. Setup profile
    await client.put(
        "/nutrition/profile",
        json={
            "sex": "male",
            "birth_year": 1980,
            "height_cm": 180,
            "weight_kg": 80.0,
            "target_weight_kg": 75.0,
            "pace_kg_per_week": 0.5,
            "meals": [
                {"type": "breakfast", "time": "08:00"},
                {"type": "lunch", "time": "13:00"},
                {"type": "dinner", "time": "19:00"},
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

    # 3. Setup product and constant dish
    prod_resp = await client.post(
        "/nutrition/products",
        json={
            "name": "Вівсяні пластівці",
            "stock_g": 500,
        },
    )
    prod_id = prod_resp.json()["id"]

    const_resp = await client.post(
        "/nutrition/dishes",
        json={
            "name": "Вівсянка",
            "kind": "constant",
            "components": [{"product_id": prod_id, "weight_g": 50}],
        },
    )
    const_dish_id = const_resp.json()["id"]

    return {"dish_id": dish_id, "const_dish_id": const_dish_id, "prod_id": prod_id}


@pytest.mark.asyncio
async def test_generate_menu_success(client: AsyncClient, session: AsyncSession):
    nutrition_data = await setup_nutrition_data(client)
    dish_id = nutrition_data["dish_id"]
    const_dish_id = nutrition_data["const_dish_id"]

    mock_gemini_data = {
        "rationale": "Збалансований раціон із білком",
        "shopping_list": ["Огірки", "Зелень"],
        "items": [
            {
                "meal_type": "lunch",
                "dish_id": dish_id,
                "title": "Індичка запечена",
                "portion_g": 200,
                "portion_text": "200 г",
                "position": 1,
            },
            {
                "meal_type": "breakfast",
                "dish_id": const_dish_id,
                "title": "Вівсянка",
                "portion_g": 50,
                "portion_text": "1 порція",
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
        types = [m["meal_type"] for m in menus]
        assert types == ["breakfast", "lunch", "dinner"]
        breakfast = [m for m in menus if m["meal_type"] == "breakfast"][0]
        lunch = [m for m in menus if m["meal_type"] == "lunch"][0]
        dinner = [m for m in menus if m["meal_type"] == "dinner"][0]
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

    # Add Breakfast manual item and mark eaten
    item1_resp = await client.post(
        "/nutrition/menus/2026-10-23/items?meal_type=breakfast",
        json={
            "title": "Яблуко",
            "portion_g": 100,
            "kcal": 50.0,
        },
    )
    menu_id = item1_resp.json()["menu_id"]
    await client.patch(
        f"/nutrition/menus/{menu_id}",
        json={"eaten_at": "2026-10-23T08:00:00Z"},
    )

    # Add Lunch manual item (planned)
    item2_resp = await client.post(
        "/nutrition/menus/2026-10-23/items?meal_type=lunch",
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
                    "meal_type": "lunch",
                    "dish_id": dish_id,
                    "title": "Індичка запечена",
                    "portion_g": 220,
                    "portion_text": "220 г",
                    "position": 1,
                },
                {
                    "meal_type": "dinner",
                    "dish_id": dish_id,
                    "title": "Індичка запечена",
                    "portion_g": 180,
                    "portion_text": "180 г",
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
        menu_by_type = {m["meal_type"]: m for m in menus}

        # Breakfast (eaten) must still be eaten and intact!
        assert menu_by_type["breakfast"]["status"] == "eaten"
        assert menu_by_type["breakfast"]["items"][0]["portion_g"] == 100

        # Lunch (planned) has been replaced by generated item
        assert menu_by_type["lunch"]["items"][0]["id"] != item2_id
        assert menu_by_type["lunch"]["items"][0]["portion_g"] == 220

        # Dinner is present
        assert "dinner" in menu_by_type


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
                    "meal_type": "lunch",
                    "dish_id": dish_id,
                    "title": "Індичка запечена",
                    "portion_g": 200,
                    "portion_text": "200 г",
                    "position": 1,
                },
                {
                    "meal_type": "dinner",
                    "dish_id": dish_id,
                    "title": "Ігнорована вечеря",
                    "portion_g": 200,
                    "portion_text": "200 г",
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
            json={"scope": "slot", "meal_type": "lunch"},
        )
        assert resp.status_code == 200
        menus = resp.json()["menus"]
        lunch = [m for m in menus if m["meal_type"] == "lunch"][0]
        assert lunch["items"] != []
        dinner = [m for m in menus if m["meal_type"] == "dinner"][0]
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

    # Breakfast: manual item with 150 kcal
    b_resp = await client.post(
        "/nutrition/menus/2026-10-23/items?meal_type=breakfast",
        json={"title": "Горіхи", "portion_g": 30, "kcal": 150.0},
    )
    b_id = b_resp.json()["id"]

    # Dinner: manual item with 200 kcal
    d_resp = await client.post(
        "/nutrition/menus/2026-10-23/items?meal_type=dinner",
        json={"title": "Салат", "portion_g": 150, "kcal": 200.0},
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
                        "meal_type": "lunch",
                        "dish_id": dish_id,
                        "title": "Індичка запечена",
                        "portion_g": 200,
                        "portion_text": "200 г",
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
            json={"scope": "slot", "meal_type": "lunch"},
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
    menus = {m["meal_type"]: m for m in day_resp.json()["menus"]}
    assert len(menus["breakfast"]["items"]) == 1
    assert menus["breakfast"]["items"][0]["id"] == b_id
    assert len(menus["dinner"]["items"]) == 1
    assert menus["dinner"]["items"][0]["id"] == d_id
    assert len(menus["lunch"]["items"]) == 1
    assert menus["lunch"]["items"][0]["title"] == "Індичка запечена"


@pytest.mark.asyncio
async def test_day_generation_replaces_unmarked_manual_items_not_eaten_or_skipped(
    client: AsyncClient,
):
    data = await setup_nutrition_data(client)
    dish_id = data["dish_id"]

    # Breakfast: manual item, mark meal eaten
    b1_resp = await client.post(
        "/nutrition/menus/2026-10-23/items?meal_type=breakfast",
        json={"title": "Яблуко", "portion_g": 100, "kcal": 50.0},
    )
    b1_id = b1_resp.json()["id"]
    b_menu_id = b1_resp.json()["menu_id"]
    await client.patch(
        f"/nutrition/menus/{b_menu_id}",
        json={"eaten_at": "2026-10-23T08:00:00Z"},
    )

    # Lunch: manual item (non-eaten meal)
    l1_resp = await client.post(
        "/nutrition/menus/2026-10-23/items?meal_type=lunch",
        json={"dish_id": dish_id, "portion_g": 150},
    )
    l1_id = l1_resp.json()["id"]

    mock_result = GeminiResult(
        data={
            "rationale": "Оновлення раціону",
            "shopping_list": [],
            "items": [
                {
                    "meal_type": "lunch",
                    "dish_id": dish_id,
                    "title": "Індичка запечена",
                    "portion_g": 200,
                    "portion_text": "200 г",
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

    day_resp = await client.get("/nutrition/menus/2026-10-23")
    menus = {m["meal_type"]: m for m in day_resp.json()["menus"]}

    # Breakfast: eaten meal kept completely intact
    b_items = menus["breakfast"]["items"]
    assert len(b_items) == 1
    assert b_items[0]["id"] == b1_id
    assert menus["breakfast"]["status"] == "eaten"

    # Lunch: non-eaten meal items replaced
    l_items = menus["lunch"]["items"]
    assert len(l_items) == 1
    assert l_items[0]["id"] != l1_id
    assert l_items[0]["origin"] == "generated"
    assert l_items[0]["title"] == "Індичка запечена"


@pytest.mark.asyncio
async def test_removed_meals_never_recreated_by_generation(
    client: AsyncClient, session: AsyncSession
):
    data = await setup_nutrition_data(client)
    dish_id = data["dish_id"]

    # Delete Lunch meal
    del_resp = await client.delete("/nutrition/menus/2026-10-23/meals/lunch")
    assert del_resp.status_code == 204

    mock_result = GeminiResult(
        data={
            "rationale": "Тест видаленого",
            "shopping_list": [],
            "items": [
                {
                    "meal_type": "breakfast",
                    "dish_id": dish_id,
                    "title": "Індичка запечена",
                    "portion_g": 200,
                    "portion_text": "200 г",
                    "position": 1,
                },
                {
                    "meal_type": "dinner",
                    "dish_id": dish_id,
                    "title": "Індичка запечена",
                    "portion_g": 180,
                    "portion_text": "180 г",
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
        assert [m["meal_type"] for m in menus] == ["breakfast", "dinner"]

    day_resp = await client.get("/nutrition/menus/2026-10-23")
    assert [m["meal_type"] for m in day_resp.json()["menus"]] == ["breakfast", "dinner"]


@pytest.mark.asyncio
async def test_409_when_nothing_to_generate_no_gemini_call(client: AsyncClient):
    await setup_nutrition_data(client)

    # Mark Breakfast, Lunch, Dinner as eaten
    for meal_type in ["breakfast", "lunch", "dinner"]:
        resp = await client.post(
            f"/nutrition/menus/2026-10-23/items?meal_type={meal_type}",
            json={"title": "Яблуко", "portion_g": 100, "kcal": 50.0},
        )
        menu_id = resp.json()["menu_id"]
        await client.patch(
            f"/nutrition/menus/{menu_id}",
            json={"eaten_at": "2026-10-23T12:00:00Z"},
        )

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
            json={"scope": "slot", "meal_type": "breakfast"},
        )
        assert resp_slot.status_code == 409
        assert "breakfast" in resp_slot.json()["detail"]
        mock_gen.assert_not_called()


@pytest.mark.asyncio
async def test_estimate_stage_runs_before_generation(client: AsyncClient):
    data = await setup_nutrition_data(client)
    dish_id = data["dish_id"]

    # Add free-text item to Breakfast without kcal
    b_resp = await client.post(
        "/nutrition/menus/2026-10-23/items?meal_type=breakfast",
        json={"title": "Яблуко", "portion_g": 120},
    )
    b_id = b_resp.json()["id"]

    calls: list[str] = []

    async def mock_gemini(prompt, model, settings, response_schema):
        if "експертний нутриціолог" in prompt:
            calls.append("estimate")
            return GeminiResult(
                data={
                    "items": [
                        {
                            "id": b_id,
                            "kcal": 60.0,
                            "protein_g": 0.5,
                            "fat_g": 0.2,
                            "carbs_g": 14.0,
                        }
                    ]
                },
                raw_text="{}",
                model="gemini-2.0-flash",
                input_tokens=50,
                output_tokens=20,
            )
        else:
            calls.append("generate")
            assert "60" in prompt
            return GeminiResult(
                data={
                    "rationale": "Обід",
                    "shopping_list": [],
                    "items": [
                        {
                            "meal_type": "lunch",
                            "dish_id": dish_id,
                            "title": "Індичка запечена",
                            "portion_g": 200,
                            "portion_text": "200 г",
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
            json={"scope": "slot", "meal_type": "lunch"},
        )
        assert resp.status_code == 200
        assert calls == ["estimate", "generate"]

    # Verify Breakfast item kcal was updated with model source
    day_resp = await client.get("/nutrition/menus/2026-10-23")
    menus = {m["meal_type"]: m for m in day_resp.json()["menus"]}
    b_item = menus["breakfast"]["items"][0]
    assert b_item["kcal"] == 60.0
    assert b_item["nutrition_source"] == "model"


@pytest.mark.asyncio
async def test_day_menu_read_flags(client: AsyncClient):
    data = await setup_nutrition_data(client)
    dish_id = data["dish_id"]

    # 1. Fresh day with projected meals (no items)
    r1 = (await client.get("/nutrition/menus/2026-10-23")).json()
    assert r1["can_generate_day"] is True
    assert r1["day_generation_replaces_manual"] is False

    # 2. Add an unmarked manual item
    i1 = (
        await client.post(
            "/nutrition/menus/2026-10-23/items?meal_type=breakfast",
            json={"title": "Яблуко", "portion_g": 100, "kcal": 50.0},
        )
    ).json()
    r2 = (await client.get("/nutrition/menus/2026-10-23")).json()
    assert r2["can_generate_day"] is True
    assert r2["day_generation_replaces_manual"] is True

    # 3. Mark the manual item's meal as eaten and add eaten items to all meals
    b_menu_id = i1["menu_id"]
    await client.patch(
        f"/nutrition/menus/{b_menu_id}",
        json={"eaten_at": "2026-10-23T08:00:00Z"},
    )
    i2 = (
        await client.post(
            "/nutrition/menus/2026-10-23/items?meal_type=lunch",
            json={"dish_id": dish_id, "portion_g": 200},
        )
    ).json()
    await client.patch(
        f"/nutrition/menus/{i2['menu_id']}",
        json={"eaten_at": "2026-10-23T13:00:00Z"},
    )
    i3 = (
        await client.post(
            "/nutrition/menus/2026-10-23/items?meal_type=dinner",
            json={"title": "Горіхи", "portion_g": 30, "kcal": 180.0},
        )
    ).json()
    await client.patch(
        f"/nutrition/menus/{i3['menu_id']}",
        json={"eaten_at": "2026-10-23T19:00:00Z"},
    )

    r3 = (await client.get("/nutrition/menus/2026-10-23")).json()
    assert r3["can_generate_day"] is False
    assert r3["day_generation_replaces_manual"] is False


@pytest.mark.asyncio
async def test_notes_flow_with_nothing_to_generate_does_not_fail(client: AsyncClient):
    await setup_nutrition_data(client)

    # Mark all meals eaten
    for meal_type in ["breakfast", "lunch", "dinner"]:
        resp = await client.post(
            f"/nutrition/menus/2026-10-23/items?meal_type={meal_type}",
            json={"title": "Яблуко", "portion_g": 100, "kcal": 50.0},
        )
        menu_id = resp.json()["menu_id"]
        await client.patch(
            f"/nutrition/menus/{menu_id}",
            json={"eaten_at": "2026-10-23T12:00:00Z"},
        )

    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_gen:
        resp = await client.post(
            "/nutrition/menus/2026-10-23/notes",
            json={"kind": "correction", "text": "Більше нічого не потрібно"},
        )
        assert resp.status_code == 201
        assert resp.json()["text"] == "Більше нічого не потрібно"
        mock_gen.assert_not_called()
