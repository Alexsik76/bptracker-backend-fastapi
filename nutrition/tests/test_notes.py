import logging
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from config import get_settings
from gemini_infra.types import GeminiInvalidResponse, GeminiResult, GeminiUnavailable
from main import app
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
                {"type": "breakfast", "time": "08:00"},
                {"type": "lunch", "time": "13:00"},
                {"type": "dinner", "time": "19:00"},
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
            "stock_g": 300,
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

    # 1. Setup Breakfast as eaten
    breakfast_resp = await client.post(
        "/nutrition/menus/2026-10-24/items?meal_type=breakfast",
        json={"title": "Яблуко", "portion_g": 100, "kcal": 50.0},
    )
    breakfast_menu_id = breakfast_resp.json()["menu_id"]
    await client.patch(
        f"/nutrition/menus/{breakfast_menu_id}",
        json={"eaten_at": "2026-10-24T08:00:00Z"},
    )

    # 2. Setup Lunch as planned (with apple)
    lunch_resp = await client.post(
        "/nutrition/menus/2026-10-24/items?meal_type=lunch",
        json={"title": "Яблуко", "portion_g": 150, "kcal": 75.0},
    )
    lunch_item_id = lunch_resp.json()["id"]

    # 3. Mock generator for correction
    mock_result = GeminiResult(
        data={
            "rationale": "Замінено казеїн на індичку запечену",
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
                    "title": "Індичка запечена",
                    "portion_g": 180,
                    "portion_text": "180 г",
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
        menu_by_type = {m["meal_type"]: m for m in menus}

        # Breakfast (eaten) must still be eaten and intact!
        assert menu_by_type["breakfast"]["status"] == "eaten"
        assert menu_by_type["breakfast"]["items"][0]["portion_g"] == 100

        # Lunch (planned) items have been replaced
        assert menu_by_type["lunch"]["items"][0]["id"] != lunch_item_id
        assert menu_by_type["lunch"]["items"][0]["dish_id"] == dish_id
        assert menu_by_type["lunch"]["items"][0]["portion_g"] == 200

        # Dinner is present
        assert "dinner" in menu_by_type

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


@pytest.mark.asyncio
async def test_menu_generation_with_off_plan_entry_succeeds(
    client: AsyncClient, session: AsyncSession
):
    env = await setup_test_environment(client)
    dish_id = env["dish_id"]

    # Add off-plan entry for 2026-10-24
    at_str = datetime(2026, 10, 24, 15, 0, tzinfo=UTC).isoformat()
    off_resp = await client.post(
        "/nutrition/off-plan",
        json={
            "date": "2026-10-24",
            "at": at_str,
            "text": "Кава з печивом 2 шт.",
            "kcal_est": 180,
        },
    )
    assert off_resp.status_code == 201

    mock_result = GeminiResult(
        data={
            "rationale": "Меню сформовано",
            "shopping_list": [],
            "items": [
                {
                    "meal_type": "breakfast",
                    "dish_id": dish_id,
                    "title": "Індичка запечена",
                    "portion_g": 200,
                    "portion_text": "200 г",
                    "position": 1,
                }
            ],
        },
        raw_text='{"rationale": "Меню сформовано", "shopping_list": [], "items": []}',
        model="gemini-2.0-flash",
        input_tokens=1000,
        output_tokens=150,
    )

    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_generate:
        mock_generate.return_value = mock_result

        resp = await client.post(
            "/nutrition/menus/2026-10-24/generate",
            json={"scope": "day"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["menus"]) >= 1

        prompt_sent = mock_generate.call_args.kwargs["prompt"]
        assert "Кава з печивом 2 шт." in prompt_sent
        assert at_str in prompt_sent
        off_plan_section = prompt_sent.split("ПОЗАПЛАНОВЕ СПОЖИВАННЯ")[1]
        off_plan_part = off_plan_section.split("ФАКТИЧНЕ СПОЖИВАННЯ")[0]
        assert "portion_g" not in off_plan_part


@pytest.mark.asyncio
async def test_info_note_with_off_plan_entry_succeeds(
    client: AsyncClient, session: AsyncSession
):
    await setup_test_environment(client)

    at_str = datetime(2026, 10, 24, 16, 0, tzinfo=UTC).isoformat()
    off_resp = await client.post(
        "/nutrition/off-plan",
        json={
            "date": "2026-10-24",
            "at": at_str,
            "text": "Яблуко",
            "kcal_est": 60,
        },
    )
    assert off_resp.status_code == 201

    mock_result = GeminiResult(
        data={"answer": "Яблуко добре вписується в денну норму."},
        raw_text='{"answer": "Яблуко добре вписується в денну норму."}',
        model="gemini-2.0-flash",
        input_tokens=800,
        output_tokens=40,
    )

    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_generate:
        mock_generate.return_value = mock_result

        resp = await client.post(
            "/nutrition/menus/2026-10-24/notes",
            json={"kind": "info", "text": "чи можна ще банан?"},
        )
        assert resp.status_code == 201
        assert resp.json()["answer"] == "Яблуко добре вписується в денну норму."

        prompt_sent = mock_generate.call_args.kwargs["prompt"]
        assert "Яблуко" in prompt_sent
        assert at_str in prompt_sent
        off_plan_section = prompt_sent.split("ПОЗАПЛАНОВЕ СПОЖИВАННЯ")[1]
        off_plan_part = off_plan_section.split("ЗАПИТАННЯ КОРИСТУВАЧА")[0]
        assert "portion_g" not in off_plan_part


@pytest.mark.asyncio
async def test_gemini_called_outside_transaction(
    client: AsyncClient, session: AsyncSession
):
    env = await setup_test_environment(client)
    dish_id = env["dish_id"]

    gen_result = GeminiResult(
        data={
            "rationale": "Меню сформовано",
            "shopping_list": [],
            "items": [
                {
                    "meal_type": "breakfast",
                    "dish_id": dish_id,
                    "title": "Індичка запечена",
                    "portion_g": 200,
                    "portion_text": "200 г",
                    "position": 1,
                }
            ],
        },
        raw_text='{"rationale": "Меню сформовано", "shopping_list": [], "items": []}',
        model="gemini-2.0-flash",
        input_tokens=500,
        output_tokens=50,
    )

    # 1. Assert session.in_transaction() is False when generate_menu calls Gemini
    gen_in_tx = None

    async def mock_generate_menu(*args, **kwargs):
        nonlocal gen_in_tx
        gen_in_tx = session.in_transaction()
        return gen_result

    with patch("gemini_infra.generate_json", side_effect=mock_generate_menu):
        resp = await client.post(
            "/nutrition/menus/2026-10-24/generate",
            json={"scope": "day"},
        )
        assert resp.status_code == 200
        assert gen_in_tx is False

    # 2. Assert session.in_transaction() is False when answer_info_note calls Gemini
    note_result = GeminiResult(
        data={"answer": "Відповідь дієтолога"},
        raw_text='{"answer": "Відповідь дієтолога"}',
        model="gemini-2.0-flash",
        input_tokens=400,
        output_tokens=30,
    )
    note_in_tx = None

    async def mock_generate_note(*args, **kwargs):
        nonlocal note_in_tx
        note_in_tx = session.in_transaction()
        return note_result

    with patch("gemini_infra.generate_json", side_effect=mock_generate_note):
        resp = await client.post(
            "/nutrition/menus/2026-10-24/notes",
            json={"kind": "info", "text": "тестове питання"},
        )
        assert resp.status_code == 201
        assert note_in_tx is False


@pytest.mark.asyncio
async def test_llm_exchange_log_db_and_stdout(
    client: AsyncClient, session: AsyncSession, caplog: pytest.LogCaptureFixture
):
    await setup_test_environment(client)

    raw_text = '{"answer": "Тестова відповідь про раціон."}'
    mock_result = GeminiResult(
        data={"answer": "Тестова відповідь про раціон."},
        raw_text=raw_text,
        model="gemini-2.0-flash",
        input_tokens=950,
        output_tokens=45,
    )

    caplog.clear()
    with caplog.at_level(logging.INFO, logger="nutrition.llm"):
        with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_gen:
            mock_gen.return_value = mock_result
            resp = await client.post(
                "/nutrition/menus/2026-10-24/notes",
                json={"kind": "info", "text": "питання про лог"},
            )
            assert resp.status_code == 201

    # Verify DB generation_log columns: prompt and duration_ms
    stmt = select(GenerationLog).where(
        GenerationLog.date == date(2026, 10, 24),
        GenerationLog.scope == "info",
    )
    logs = (await session.exec(stmt)).all()
    assert len(logs) == 1
    log_entry = logs[0]
    assert log_entry.prompt is not None
    assert "питання про лог" in log_entry.prompt
    assert log_entry.duration_ms is not None
    assert isinstance(log_entry.duration_ms, int)
    assert log_entry.duration_ms >= 0
    assert log_entry.raw_response == raw_text

    # Verify stdout logger nutrition.llm emitted record
    llm_records = [r for r in caplog.records if r.name == "nutrition.llm"]
    assert len(llm_records) == 1
    record_msg = llm_records[0].getMessage()
    assert f"user_id={log_entry.user_id}" in record_msg
    assert "date=2026-10-24" in record_msg
    assert "scope=info" in record_msg
    assert "model=gemini-2.0-flash" in record_msg
    assert "prompt_version=" in record_msg
    assert f"duration_ms={log_entry.duration_ms}" in record_msg
    assert "tokens=(input=950, output=45)" in record_msg
    assert log_entry.prompt in record_msg
    assert raw_text in record_msg


@pytest.mark.asyncio
async def test_llm_exchange_log_disabled_switches_stdout_off(
    client: AsyncClient, session: AsyncSession, caplog: pytest.LogCaptureFixture
):
    await setup_test_environment(client)

    mock_result = GeminiResult(
        data={"answer": "Відповідь"},
        raw_text='{"answer": "Відповідь"}',
        model="gemini-2.0-flash",
        input_tokens=200,
        output_tokens=20,
    )

    current_settings = get_settings().model_copy()
    current_settings.llm_exchange_log = False
    app.dependency_overrides[get_settings] = lambda: current_settings

    try:
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="nutrition.llm"):
            with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_gen:
                mock_gen.return_value = mock_result
                resp = await client.post(
                    "/nutrition/menus/2026-10-24/notes",
                    json={"kind": "info", "text": "тест відключеного логу"},
                )
                assert resp.status_code == 201
    finally:
        app.dependency_overrides.pop(get_settings, None)

    # Stdout record was switched off
    llm_records = [r for r in caplog.records if r.name == "nutrition.llm"]
    assert len(llm_records) == 0

    # DB columns were still written
    stmt = select(GenerationLog).where(
        GenerationLog.date == date(2026, 10, 24),
        GenerationLog.scope == "info",
    )
    logs = (await session.exec(stmt)).all()
    assert len(logs) == 1
    assert logs[0].prompt is not None
    assert logs[0].duration_ms is not None


@pytest.mark.asyncio
async def test_gemini_unavailable_logs_to_db(
    client: AsyncClient, session: AsyncSession
):
    await setup_test_environment(client)

    # 1. Info note unavailable -> 502, writes row with raw_response = NULL
    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_gen:
        mock_gen.side_effect = GeminiUnavailable("Timeout connecting to Gemini")
        resp = await client.post(
            "/nutrition/menus/2026-10-24/notes",
            json={"kind": "info", "text": "таймаут тест"},
        )
        assert resp.status_code == 502

    stmt = select(GenerationLog).where(
        GenerationLog.date == date(2026, 10, 24),
        GenerationLog.scope == "info",
    )
    logs = (await session.exec(stmt)).all()
    assert len(logs) == 1
    assert logs[0].raw_response is None
    assert logs[0].prompt is not None
    assert "таймаут тест" in logs[0].prompt
    assert logs[0].duration_ms is not None
    assert logs[0].input_tokens is None
    assert logs[0].output_tokens is None

    # 2. Menu generation unavailable -> 502, writes row with raw_response = NULL
    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_gen:
        mock_gen.side_effect = GeminiUnavailable("Service down")
        resp2 = await client.post(
            "/nutrition/menus/2026-10-24/generate",
            json={"scope": "day"},
        )
        assert resp2.status_code == 502

    stmt_gen = select(GenerationLog).where(
        GenerationLog.date == date(2026, 10, 24),
        GenerationLog.scope == "day",
    )
    gen_logs = (await session.exec(stmt_gen)).all()
    assert len(gen_logs) == 1
    assert gen_logs[0].raw_response is None
    assert gen_logs[0].prompt is not None
    assert gen_logs[0].duration_ms is not None
    assert gen_logs[0].input_tokens is None
    assert gen_logs[0].output_tokens is None

