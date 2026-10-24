from datetime import date
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from gemini_infra.types import GeminiInvalidResponse, GeminiResult, GeminiUnavailable
from nutrition.models import GenerationLog, MenuNote, MenuNoteKind


async def setup_test_environment(client: AsyncClient):
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
            "restrictions": "Без жовчного міхура",
            "dislikes": "Печінка",
            "meals": [
                {"name": "Сніданок", "time": "08:00"},
                {"name": "Обід", "time": "13:00"},
                {"name": "Вечеря", "time": "19:00"},
            ],
        },
    )

    # Setup ready dish
    dish_resp = await client.post(
        "/nutrition/dishes",
        json={
            "name": "Індичка запечена",
            "kind": "ready",
            "cooked_weight_g": 600,
            "cooked_on": "2026-10-24",
        },
    )
    dish_id = dish_resp.json()["id"]

    # Setup product
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
async def test_menu_note_info_success(client: AsyncClient, session: AsyncSession):
    await setup_test_environment(client)

    mock_result = GeminiResult(
        data={"answer": "Можна з'їсти до 150 г винограду, краще в першій половині дня."},
        raw_text='{"answer": "Можна з\'їсти до 150 г винограду, краще в першій половині дня."}',
        model="gemini-2.0-flash",
        input_tokens=800,
        output_tokens=50,
    )

    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_generate:
        mock_generate.return_value = mock_result

        resp = await client.post(
            "/nutrition/menus/2026-10-24/notes",
            json={
                "kind": "info",
                "text": "є виноград — скільки можна",
            },
        )
        assert resp.status_code == 201
        data = resp.json()

        assert data["kind"] == "info"
        assert data["text"] == "є виноград — скільки можна"
        assert data["answer"] == "Можна з'їсти до 150 г винограду, краще в першій половині дня."
        assert data["date"] == "2026-10-24"
        assert "id" in data
        assert "created_at" in data

        # Verify saved in menu_notes table
        stmt = select(MenuNote).where(MenuNote.date == date(2026, 10, 24))
        notes = (await session.exec(stmt)).all()
        assert len(notes) == 1
        assert notes[0].kind == MenuNoteKind.INFO
        assert notes[0].answer == "Можна з'їсти до 150 г винограду, краще в першій половині дня."

        # Verify logged in generation_log with scope info
        log_stmt = select(GenerationLog).where(
            GenerationLog.date == date(2026, 10, 24),
            GenerationLog.scope == "info",
        )
        logs = (await session.exec(log_stmt)).all()
        assert len(logs) == 1
        assert logs[0].input_tokens == 800
        assert logs[0].output_tokens == 50


@pytest.mark.asyncio
async def test_menu_note_correction_regenerates_unmarked_items(
    client: AsyncClient, session: AsyncSession
):
    env = await setup_test_environment(client)
    dish_id = env["dish_id"]
    prod_id = env["prod_id"]

    # 1. Setup Breakfast as eaten
    breakfast_resp = await client.post(
        "/nutrition/menus/2026-10-24/items?slot=Сніданок",
        json={"product_id": prod_id, "portion_g": 100},
    )
    breakfast_menu_id = breakfast_resp.json()["menu_id"]
    await client.patch(f"/nutrition/menus/{breakfast_menu_id}", json={"status": "eaten"})

    # 2. Setup Lunch as planned (with apple)
    lunch_resp = await client.post(
        "/nutrition/menus/2026-10-24/items?slot=Обід",
        json={"product_id": prod_id, "portion_g": 150},
    )
    lunch_item_id = lunch_resp.json()["id"]

    # 3. Mock generator for correction
    mock_result = GeminiResult(
        data={
            "rationale": "Замінено казеїн на індичку запечену",
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
                    "title": "Індичка запечена",
                    "portion_g": 180,
                    "portion_text": "180 г",
                    "kcal_est": 230,
                    "position": 1,
                },
            ],
        },
        raw_text="{}",
        model="gemini-2.0-flash",
        input_tokens=1200,
        output_tokens=250,
    )

    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_generate:
        mock_generate.return_value = mock_result

        resp = await client.post(
            "/nutrition/menus/2026-10-24/notes",
            json={
                "kind": "correction",
                "text": "не хочу сьогодні казеїн, заміни",
            },
        )
        assert resp.status_code == 201
        data = resp.json()

        assert data["kind"] == "correction"
        assert data["text"] == "не хочу сьогодні казеїн, заміни"
        assert data["answer"] is None

        # Verify Gemini prompt had correction and unmarked scope
        assert mock_generate.called
        call_kwargs = mock_generate.call_args.kwargs
        prompt_sent = call_kwargs["prompt"]
        assert "не хочу сьогодні казеїн, заміни" in prompt_sent
        assert "unmarked" in prompt_sent

        # Check menus of the day
        menu_resp = await client.get("/nutrition/menus/2026-10-24")
        assert menu_resp.status_code == 200
        menus = menu_resp.json()["menus"]
        menu_by_slot = {m["slot"]: m for m in menus}

        # Breakfast (eaten) must still be eaten and intact!
        assert menu_by_slot["Сніданок"]["status"] == "eaten"
        assert menu_by_slot["Сніданок"]["items"][0]["portion_g"] == 100

        # Lunch (planned) items have been replaced
        assert menu_by_slot["Обід"]["items"][0]["id"] != lunch_item_id
        assert menu_by_slot["Обід"]["items"][0]["dish_id"] == dish_id
        assert menu_by_slot["Обід"]["items"][0]["portion_g"] == 200

        # Dinner is present
        assert "Вечеря" in menu_by_slot

        # Verify generation log has scope unmarked
        log_stmt = select(GenerationLog).where(
            GenerationLog.date == date(2026, 10, 24),
            GenerationLog.scope == "unmarked",
        )
        logs = (await session.exec(log_stmt)).all()
        assert len(logs) == 1


@pytest.mark.asyncio
async def test_menu_note_missing_profile_returns_409(client: AsyncClient):
    resp = await client.post(
        "/nutrition/menus/2026-10-24/notes",
        json={"kind": "info", "text": "якась порада"},
    )
    assert resp.status_code == 409
    assert "Nutrition profile not found" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_menu_note_empty_text_returns_400(client: AsyncClient):
    await setup_test_environment(client)
    resp = await client.post(
        "/nutrition/menus/2026-10-24/notes",
        json={"kind": "info", "text": "   "},
    )
    assert resp.status_code == 400
    assert "cannot be empty" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_menu_note_gemini_unavailable_returns_502(client: AsyncClient):
    await setup_test_environment(client)
    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_generate:
        mock_generate.side_effect = GeminiUnavailable()

        resp = await client.post(
            "/nutrition/menus/2026-10-24/notes",
            json={"kind": "info", "text": "питання"},
        )
        assert resp.status_code == 502
        assert resp.json()["detail"] == "Gemini service is unavailable"


@pytest.mark.asyncio
async def test_menu_note_invalid_gemini_response_returns_422(
    client: AsyncClient, session: AsyncSession
):
    await setup_test_environment(client)
    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_generate:
        mock_generate.side_effect = GeminiInvalidResponse(
            "Broken JSON", raw_text='{"malformed": true'
        )

        resp = await client.post(
            "/nutrition/menus/2026-10-24/notes",
            json={"kind": "info", "text": "питання"},
        )
        assert resp.status_code == 422
        assert "Invalid Gemini response" in resp.json()["detail"]

        stmt = select(GenerationLog).where(GenerationLog.raw_response == '{"malformed": true')
        log = (await session.exec(stmt)).first()
        assert log is not None
        assert log.scope == "info"


@pytest.mark.asyncio
async def test_menu_note_user_isolation(client_factory, make_user):
    user_a = await make_user("user_a@example.com")
    user_b = await make_user("user_b@example.com")

    client_a = client_factory(user_a)
    await setup_test_environment(client_a)

    mock_result = GeminiResult(
        data={"answer": "Відповідь для А"},
        raw_text="{}",
        model="gemini-2.0-flash",
        input_tokens=100,
        output_tokens=20,
    )

    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_generate:
        mock_generate.return_value = mock_result
        resp_a = await client_a.post(
            "/nutrition/menus/2026-10-24/notes",
            json={"kind": "info", "text": "питання А"},
        )
        assert resp_a.status_code == 201
        note_a = resp_a.json()
        assert note_a["user_id"] == str(user_a)

    # Now act as User B
    client_b = client_factory(user_b)
    await setup_test_environment(client_b)

    # Context for User B's correction must NOT contain User A's note
    mock_correction = GeminiResult(
        data={"rationale": "Меню для B", "shopping_list": [], "items": []},
        raw_text="{}",
        model="gemini-2.0-flash",
        input_tokens=100,
        output_tokens=20,
    )
    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_generate_b:
        mock_generate_b.return_value = mock_correction
        resp_b = await client_b.post(
            "/nutrition/menus/2026-10-24/notes",
            json={"kind": "correction", "text": "корекція В"},
        )
        assert resp_b.status_code == 201

        prompt_b = mock_generate_b.call_args.kwargs["prompt"]
        assert "питання А" not in prompt_b
        assert "корекція В" in prompt_b


@pytest.mark.asyncio
async def test_correction_note_persists_on_gemini_unavailable_502(
    client: AsyncClient, session: AsyncSession
):
    await setup_test_environment(client)
    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_generate:
        mock_generate.side_effect = GeminiUnavailable()

        resp = await client.post(
            "/nutrition/menus/2026-10-24/notes",
            json={"kind": "correction", "text": "не хочу молочне"},
        )
        assert resp.status_code == 502

        stmt = select(MenuNote).where(
            MenuNote.date == date(2026, 10, 24),
            MenuNote.kind == MenuNoteKind.CORRECTION,
        )
        notes = (await session.exec(stmt)).all()
        assert len(notes) == 1
        assert notes[0].text == "не хочу молочне"


@pytest.mark.asyncio
async def test_correction_note_persists_on_invalid_response_422(
    client: AsyncClient, session: AsyncSession
):
    await setup_test_environment(client)
    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_generate:
        mock_generate.side_effect = GeminiInvalidResponse(
            "Broken JSON", raw_text='{"malformed": true'
        )

        resp = await client.post(
            "/nutrition/menus/2026-10-24/notes",
            json={"kind": "correction", "text": "додай яблуко"},
        )
        assert resp.status_code == 422

        stmt = select(MenuNote).where(
            MenuNote.date == date(2026, 10, 24),
            MenuNote.kind == MenuNoteKind.CORRECTION,
        )
        notes = (await session.exec(stmt)).all()
        assert len(notes) == 1
        assert notes[0].text == "додай яблуко"

