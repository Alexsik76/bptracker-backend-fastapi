from datetime import UTC, date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from gemini_infra import GeminiResult
from nutrition.clock import utc_today
from nutrition.models import GenerationLog, Menu, MenuState, PinnedDishDay


@pytest.mark.asyncio
async def test_utc_today():
    assert utc_today() == datetime.now(UTC).date()


@pytest.mark.asyncio
async def test_menu_crud_flow(client: AsyncClient):
    today = utc_today()
    today_dt = datetime.now(UTC)

    # 1. Create a ready dish with nutrition
    dish_resp = await client.post(
        "/nutrition/dishes",
        json={
            "name": "Вівсянка",
            "is_ready": True,
            "kcal": 120.0,
            "protein_g": 4.0,
            "fat_g": 2.5,
            "carbs_g": 20.0,
        },
    )
    assert dish_resp.status_code == 201
    dish_id = dish_resp.json()["id"]

    # 2. Create eaten meal with item
    meal_payload = {
        "date": today.isoformat(),
        "meal_type": "breakfast",
        "origin": "manual",
        "state": "eaten",
        "eaten_at": today_dt.isoformat(),
        "items": [
            {
                "dish_id": dish_id,
                "portion_g": 200,
            }
        ],
    }
    meal_resp = await client.post("/nutrition/menus", json=meal_payload)
    assert meal_resp.status_code == 201
    meal_data = meal_resp.json()
    meal_id = meal_data["id"]
    assert meal_data["date"] == today.isoformat()
    assert meal_data["meal_type"] == "breakfast"
    assert meal_data["origin"] == "manual"
    assert meal_data["state"] == "eaten"
    assert len(meal_data["items"]) == 1
    item = meal_data["items"][0]
    item_id = item["id"]
    assert item["title"] == "Вівсянка"
    assert item["portion_g"] == 200
    assert item["portion_text"] == "200 г"
    # Ratio = 200/100 = 2.0 -> kcal = 120*2 = 240
    assert item["kcal"] == 240.0
    assert item["protein_g"] == 8.0
    assert item["nutrition_source"] == "computed"

    # 3. Create another meal with the SAME meal_type on the same day (no uniqueness restriction!)
    second_meal_payload = {
        "date": today.isoformat(),
        "meal_type": "breakfast",
        "origin": "manual",
        "state": "eaten",
        "eaten_at": (today_dt + timedelta(hours=2)).isoformat(),
        "items": [
            {
                "title": "Кава з молоком",
                "portion_g": 150,
                "kcal": 50.0,
            }
        ],
    }
    second_resp = await client.post("/nutrition/menus", json=second_meal_payload)
    assert second_resp.status_code == 201
    assert second_resp.json()["meal_type"] == "breakfast"

    # 4. Create a suggestion meal (eaten_at is None)
    sugg_payload = {
        "date": today.isoformat(),
        "meal_type": "lunch",
        "origin": "suggestion",
        "state": "suggestion",
        "eaten_at": None,
        "items": [
            {
                "dish_id": dish_id,
                "portion_g": 300,
            }
        ],
    }
    sugg_resp = await client.post("/nutrition/menus", json=sugg_payload)
    assert sugg_resp.status_code == 201
    sugg_data = sugg_resp.json()
    assert sugg_data["state"] == "suggestion"
    assert sugg_data["eaten_at"] is None

    # 5. List meals for today: should include both eaten meals and the current suggestion
    list_resp = await client.get(f"/nutrition/menus?date={today.isoformat()}")
    assert list_resp.status_code == 200
    day_meals = list_resp.json()
    assert len(day_meals) == 3
    ids = [m["id"] for m in day_meals]
    assert meal_id in ids
    assert sugg_data["id"] in ids

    # 6. Get single meal
    get_meal_resp = await client.get(f"/nutrition/menus/{meal_id}")
    assert get_meal_resp.status_code == 200
    assert get_meal_resp.json()["id"] == meal_id

    # 7. Change grams of a menu item with recomputed snapshot
    patch_item_resp = await client.patch(
        f"/nutrition/menu-items/{item_id}",
        json={"portion_g": 300},
    )
    assert patch_item_resp.status_code == 200
    patched_item = patch_item_resp.json()
    assert patched_item["portion_g"] == 300
    assert patched_item["portion_text"] == "300 г"
    # Ratio = 300/100 = 3.0 -> kcal = 120*3 = 360
    assert patched_item["kcal"] == 360.0
    assert patched_item["protein_g"] == 12.0
    assert patched_item["nutrition_source"] == "computed"

    # 8. Delete menu item
    del_item_resp = await client.delete(f"/nutrition/menu-items/{item_id}")
    assert del_item_resp.status_code == 204
    # Check item is gone from meal
    meal_after_item_del = (await client.get(f"/nutrition/menus/{meal_id}")).json()
    assert len(meal_after_item_del["items"]) == 0

    # 9. Delete meal
    del_meal_resp = await client.delete(f"/nutrition/menus/{meal_id}")
    assert del_meal_resp.status_code == 204
    assert (await client.get(f"/nutrition/menus/{meal_id}")).status_code == 404


@pytest.mark.asyncio
async def test_recompute_snapshot_for_free_text_item(client: AsyncClient):
    today = utc_today()
    # Free-text item with initial kcal and grams
    meal = (
        await client.post(
            "/nutrition/menus",
            json={
                "date": today.isoformat(),
                "meal_type": "afternoon_snack",
                "items": [
                    {
                        "title": "Печиво",
                        "portion_g": 50,
                        "kcal": 200.0,
                        "protein_g": 4.0,
                    }
                ],
            },
        )
    ).json()
    item_id = meal["items"][0]["id"]

    # Change grams to 100g -> ratio = 100/50 = 2.0 -> kcal=400, protein=8
    patch_resp = await client.patch(
        f"/nutrition/menu-items/{item_id}",
        json={"portion_g": 100},
    )
    assert patch_resp.status_code == 200
    data = patch_resp.json()
    assert data["portion_g"] == 100
    assert data["kcal"] == 400.0
    assert data["protein_g"] == 8.0


@pytest.mark.asyncio
async def test_menu_user_isolation(client_factory, make_user):
    today = utc_today()
    user_a = await make_user("ma@example.com")
    user_b = await make_user("mb@example.com")

    client_a = client_factory(user_a)

    meal_a = (
        await client_a.post(
            "/nutrition/menus",
            json={
                "date": today.isoformat(),
                "meal_type": "lunch",
                "items": [{"title": "Суп", "portion_g": 250, "kcal": 150.0}],
            },
        )
    ).json()
    meal_a_id = meal_a["id"]
    item_a_id = meal_a["items"][0]["id"]

    client_b = client_factory(user_b)

    # User B cannot see or touch User A's meal or item
    list_b = (await client_b.get(f"/nutrition/menus?date={today.isoformat()}")).json()
    assert not any(m["id"] == meal_a_id for m in list_b)
    assert (await client_b.get(f"/nutrition/menus/{meal_a_id}")).status_code == 404
    assert (await client_b.delete(f"/nutrition/menus/{meal_a_id}")).status_code == 404
    assert (
        await client_b.patch(f"/nutrition/menu-items/{item_a_id}", json={"portion_g": 300})
    ).status_code == 404
    assert (await client_b.delete(f"/nutrition/menu-items/{item_a_id}")).status_code == 404


@pytest.mark.asyncio
async def test_not_found_returns_404(client: AsyncClient):
    random_id = uuid4()
    assert (await client.get(f"/nutrition/menus/{random_id}")).status_code == 404
    assert (await client.delete(f"/nutrition/menus/{random_id}")).status_code == 404
    patch_meal = await client.patch(f"/nutrition/menus/{random_id}", json={"state": "eaten"})
    assert patch_meal.status_code == 404
    patch_item = await client.patch(
        f"/nutrition/menu-items/{random_id}", json={"portion_g": 100}
    )
    assert patch_item.status_code == 404
    assert (await client.delete(f"/nutrition/menu-items/{random_id}")).status_code == 404


@pytest.mark.asyncio
async def test_suggestion_without_text_creates_one_suggestion_and_log(
    client: AsyncClient, session: AsyncSession, test_user_id: UUID
):
    """Suggestion without text creates one suggestion and a generation_log row.

    Must include prompt and response in generation_log.
    """
    test_date = date(2026, 11, 3)
    test_at = datetime(2026, 11, 3, 12, 0, 0, tzinfo=UTC)

    mock_gemini_res = GeminiResult(
        data={
            "meal_type": "lunch",
            "items": [
                {
                    "name": "Гречка",
                    "dish_id": None,
                    "grams": 150,
                    "kcal": 180.0,
                    "protein_g": 6.0,
                    "fat_g": 2.0,
                    "carbs_g": 35.0,
                },
                {
                    "name": "Курка",
                    "dish_id": None,
                    "grams": 120,
                    "kcal": 200.0,
                    "protein_g": 30.0,
                    "fat_g": 5.0,
                    "carbs_g": 0.0,
                },
            ],
        },
        raw_text='{"mock": "response"}',
        model="gemini-2.5-flash",
        input_tokens=120,
        output_tokens=60,
    )

    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_gemini:
        mock_gemini.return_value = mock_gemini_res
        resp = await client.post(
            "/nutrition/suggestions",
            json={
                "text": None,
                "date": test_date.isoformat(),
                "at": test_at.isoformat(),
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "suggest"
    menu = body["menu"]
    assert menu["state"] == "suggestion"
    assert menu["origin"] == "suggestion"
    assert menu["meal_type"] == "lunch"
    assert menu["date"] == test_date.isoformat()
    assert len(menu["items"]) == 2

    # Check generation_log row
    log_rows = (
        await session.exec(
            select(GenerationLog).where(
                GenerationLog.user_id == test_user_id,
                GenerationLog.date == test_date,
                GenerationLog.scope == "suggestion",
            )
        )
    ).all()
    assert len(log_rows) == 1
    log = log_rows[0]
    assert log.menu_id == UUID(menu["id"])
    assert log.model == "gemini-2.5-flash"
    assert log.raw_response == '{"mock": "response"}'
    assert log.prompt is not None
    assert "--- ПОТОЧНИЙ ЧАС ТА ЗАПИТ ---" in log.prompt
    assert log.input_tokens == 120
    assert log.output_tokens == 60


@pytest.mark.asyncio
async def test_second_suggestion_request_replaces_previous(
    client: AsyncClient, session: AsyncSession, test_user_id: UUID
):
    """A second suggestion request replaces the user's previous suggestion (at most one in DB)."""
    test_date = date(2026, 11, 3)
    test_at = datetime(2026, 11, 3, 12, 0, 0, tzinfo=UTC)

    first_res = GeminiResult(
        data={
            "meal_type": "lunch",
            "items": [
                {
                    "name": "Суп",
                    "dish_id": None,
                    "grams": 250,
                    "kcal": 150.0,
                    "protein_g": 5.0,
                    "fat_g": 3.0,
                    "carbs_g": 20.0,
                }
            ],
        },
        raw_text='{"first": true}',
        model="gemini-2.5-flash",
        input_tokens=100,
        output_tokens=40,
    )

    second_res = GeminiResult(
        data={
            "meal_type": "dinner",
            "items": [
                {
                    "name": "Омлет",
                    "dish_id": None,
                    "grams": 200,
                    "kcal": 240.0,
                    "protein_g": 18.0,
                    "fat_g": 16.0,
                    "carbs_g": 2.0,
                }
            ],
        },
        raw_text='{"second": true}',
        model="gemini-2.5-flash",
        input_tokens=110,
        output_tokens=45,
    )

    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_gemini:
        mock_gemini.return_value = first_res
        resp1 = await client.post(
            "/nutrition/suggestions",
            json={
                "text": "обід",
                "date": test_date.isoformat(),
                "at": test_at.isoformat(),
            },
        )
        assert resp1.status_code == 200
        first_id = resp1.json()["menu"]["id"]

        mock_gemini.return_value = second_res
        resp2 = await client.post(
            "/nutrition/suggestions",
            json={
                "text": "краще вечерю",
                "date": test_date.isoformat(),
                "at": test_at.isoformat(),
            },
        )
        assert resp2.status_code == 200
        second_id = resp2.json()["menu"]["id"]

    assert first_id != second_id

    # Verify DB: at most one suggestion menu exists for user
    db_suggestions = (
        await session.exec(
            select(Menu).where(
                Menu.user_id == test_user_id,
                Menu.state == MenuState.SUGGESTION,
            )
        )
    ).all()
    assert len(db_suggestions) == 1
    assert str(db_suggestions[0].id) == second_id
    assert db_suggestions[0].meal_type.value == "dinner"


@pytest.mark.asyncio
async def test_eat_moves_suggestion_to_eaten_and_journal_totals_grow(
    client: AsyncClient, session: AsyncSession, test_user_id: UUID
):
    """Eat moves suggestion to eaten and journal totals grow."""
    test_date = date(2026, 11, 3)
    test_at = datetime(2026, 11, 3, 13, 0, 0, tzinfo=UTC)

    mock_gemini_res = GeminiResult(
        data={
            "meal_type": "lunch",
            "items": [
                {
                    "name": "Рис з рибою",
                    "dish_id": None,
                    "grams": 300,
                    "kcal": 450.0,
                    "protein_g": 35.0,
                    "fat_g": 10.0,
                    "carbs_g": 50.0,
                }
            ],
        },
        raw_text="{}",
        model="gemini-2.5-flash",
        input_tokens=100,
        output_tokens=50,
    )

    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_gemini:
        mock_gemini.return_value = mock_gemini_res
        sugg_resp = await client.post(
            "/nutrition/suggestions",
            json={
                "text": None,
                "date": test_date.isoformat(),
                "at": test_at.isoformat(),
            },
        )
    sugg_menu = sugg_resp.json()["menu"]
    sugg_id = sugg_menu["id"]

    # Initial journal: totals should be 0, suggestion is present
    j_before = (await client.get(f"/nutrition/journal?date={test_date.isoformat()}")).json()
    assert j_before["totals"]["kcal"] == 0.0
    assert j_before["totals"]["protein_g"] == 0.0
    assert len(j_before["meals"]) == 0
    assert j_before["suggestion"]["id"] == sugg_id

    # Call /eat
    eat_resp = await client.post(
        f"/nutrition/menus/{sugg_id}/eat",
        json={
            "date": test_date.isoformat(),
            "at": test_at.isoformat(),
        },
    )
    assert eat_resp.status_code == 200
    eaten_menu = eat_resp.json()
    assert eaten_menu["state"] == "eaten"
    assert datetime.fromisoformat(eaten_menu["eaten_at"]) == test_at
    assert eaten_menu["date"] == test_date.isoformat()

    # Journal after eat: totals grew, meals has 1 eaten menu, suggestion is null
    j_after = (await client.get(f"/nutrition/journal?date={test_date.isoformat()}")).json()
    assert j_after["totals"]["kcal"] == 450.0
    assert j_after["totals"]["protein_g"] == 35.0
    assert len(j_after["meals"]) == 1
    assert j_after["meals"][0]["id"] == sugg_id
    assert j_after["suggestion"] is None


@pytest.mark.asyncio
async def test_log_intent_creates_manual_eaten_menu_with_items(
    client: AsyncClient, session: AsyncSession, test_user_id: UUID
):
    """'log' intent creates a manual eaten menu with two items; current suggestion stays."""
    test_date = date(2026, 11, 3)
    test_at = datetime(2026, 11, 3, 9, 30, 0, tzinfo=UTC)

    # 1. First, create a suggestion so we can verify it stays untouched on log
    sugg_gemini_res = GeminiResult(
        data={
            "meal_type": "lunch",
            "items": [
                {
                    "name": "Борщ",
                    "dish_id": None,
                    "grams": 350,
                    "kcal": 220.0,
                    "protein_g": 8.0,
                    "fat_g": 6.0,
                    "carbs_g": 25.0,
                }
            ],
        },
        raw_text="{}",
        model="gemini-2.5-flash",
        input_tokens=100,
        output_tokens=50,
    )
    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_gemini:
        mock_gemini.return_value = sugg_gemini_res
        sugg_resp = await client.post(
            "/nutrition/suggestions",
            json={"text": None, "date": test_date.isoformat(), "at": test_at.isoformat()},
        )
    sugg_id = sugg_resp.json()["menu"]["id"]

    # 2. User logs already eaten food: "з'їв омлет 200 г і яблуко"
    log_gemini_res = GeminiResult(
        data={
            "meal_type": "breakfast",
            "items": [
                {
                    "name": "Омлет",
                    "dish_id": None,
                    "grams": 200,
                    "kcal": 250.0,
                    "protein_g": 18.0,
                    "fat_g": 17.0,
                    "carbs_g": 3.0,
                },
                {
                    "name": "Яблуко",
                    "dish_id": None,
                    "grams": 150,
                    "kcal": 75.0,
                    "protein_g": 0.5,
                    "fat_g": 0.3,
                    "carbs_g": 18.0,
                },
            ],
        },
        raw_text='{"log": true}',
        model="gemini-2.5-flash",
        input_tokens=130,
        output_tokens=70,
    )

    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_gemini:
        mock_gemini.return_value = log_gemini_res
        log_resp = await client.post(
            "/nutrition/suggestions",
            json={
                "mode": "log",
                "text": "з'їв омлет 200 г і яблуко",
                "date": test_date.isoformat(),
                "at": test_at.isoformat(),
            },
        )

    assert log_resp.status_code == 200
    log_body = log_resp.json()
    assert log_body["mode"] == "log"
    logged_menu = log_body["menu"]
    assert logged_menu["state"] == "eaten"
    assert logged_menu["origin"] == "manual"
    assert datetime.fromisoformat(logged_menu["eaten_at"]) == test_at
    assert logged_menu["date"] == test_date.isoformat()
    assert len(logged_menu["items"]) == 2

    # Suggestion must still exist!
    j = (await client.get(f"/nutrition/journal?date={test_date.isoformat()}")).json()
    assert j["suggestion"] is not None
    assert j["suggestion"]["id"] == sugg_id
    assert len(j["meals"]) == 1
    assert j["meals"][0]["id"] == logged_menu["id"]


@pytest.mark.asyncio
async def test_pinned_dish_in_journal_and_not_recreated_after_deletion(
    client: AsyncClient, session: AsyncSession, test_user_id: UUID
):
    """Pinned dish appears once in journal with origin=pinned and does not return after deletion."""
    test_date = date(2026, 11, 3)

    # 1. Create a pinned dish
    dish_resp = await client.post(
        "/nutrition/dishes",
        json={
            "name": "Сирники",
            "is_ready": True,
            "pinned_meal_type": "breakfast",
            "kcal": 210.0,
            "protein_g": 16.0,
            "fat_g": 8.0,
            "carbs_g": 18.0,
        },
    )
    assert dish_resp.status_code == 201
    dish_id = dish_resp.json()["id"]

    # 2. First GET /journal: materializes the pinned dish
    j1 = (await client.get(f"/nutrition/journal?date={test_date.isoformat()}")).json()
    assert len(j1["meals"]) == 1
    pinned_meal = j1["meals"][0]
    pinned_meal_id = pinned_meal["id"]
    assert pinned_meal["origin"] == "pinned"
    assert pinned_meal["state"] == "eaten"
    assert pinned_meal["meal_type"] == "breakfast"
    assert pinned_meal["eaten_at"] is None
    assert pinned_meal["pinned_dish_id"] == dish_id
    assert len(pinned_meal["items"]) == 1
    assert pinned_meal["items"][0]["portion_g"] == 100
    assert pinned_meal["items"][0]["kcal"] == 210.0

    # Marker must exist in pinned_dish_days
    markers = (
        await session.exec(
            select(PinnedDishDay).where(
                PinnedDishDay.user_id == test_user_id,
                PinnedDishDay.date == test_date,
            )
        )
    ).all()
    assert len(markers) == 1
    assert str(markers[0].dish_id) == dish_id

    # 3. Second GET /journal on same date: does NOT create a duplicate
    j2 = (await client.get(f"/nutrition/journal?date={test_date.isoformat()}")).json()
    assert len(j2["meals"]) == 1
    assert j2["meals"][0]["id"] == pinned_meal_id

    # 4. Delete the pinned meal
    del_resp = await client.delete(f"/nutrition/menus/{pinned_meal_id}")
    assert del_resp.status_code == 204

    # 5. Third GET /journal: meal is gone and NOT recreated because marker remains
    j3 = (await client.get(f"/nutrition/journal?date={test_date.isoformat()}")).json()
    assert len(j3["meals"]) == 0

    # Marker still exists
    markers_after = (
        await session.exec(
            select(PinnedDishDay).where(
                PinnedDishDay.user_id == test_user_id,
                PinnedDishDay.date == test_date,
            )
        )
    ).all()
    assert len(markers_after) == 1


@pytest.mark.asyncio
async def test_evaluation_scheduled_after_journal_writes_with_menu_id(
    client: AsyncClient, session: AsyncSession, test_user_id: UUID
):
    """Evaluation is scheduled as background task after journal writes with menu_id."""
    test_date = date(2026, 11, 3)
    test_at = datetime(2026, 11, 3, 14, 0, 0, tzinfo=UTC)

    with patch("nutrition.router.menu.evaluate_menu_day_background") as mock_eval:
        # 1. Suggestion: evaluation must NOT be scheduled
        sugg_gemini_res = GeminiResult(
            data={
                "meal_type": "lunch",
                "items": [
                    {
                        "name": "Салат",
                        "dish_id": None,
                        "grams": 200,
                        "kcal": 100.0,
                        "protein_g": 2.0,
                        "fat_g": 5.0,
                        "carbs_g": 10.0,
                    }
                ],
            },
            raw_text="{}",
            model="gemini-2.5-flash",
            input_tokens=100,
            output_tokens=50,
        )
        with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_gemini:
            mock_gemini.return_value = sugg_gemini_res
            sugg_resp = await client.post(
                "/nutrition/suggestions",
                json={"text": None, "date": test_date.isoformat(), "at": test_at.isoformat()},
            )
        assert sugg_resp.status_code == 200
        sugg_id = sugg_resp.json()["menu"]["id"]
        assert mock_eval.call_count == 0

        # 2. Eat suggestion: evaluation IS scheduled with menu_id
        eat_resp = await client.post(
            f"/nutrition/menus/{sugg_id}/eat",
            json={"date": test_date.isoformat(), "at": test_at.isoformat()},
        )
        assert eat_resp.status_code == 200
        assert mock_eval.call_count == 1
        _, kwargs = mock_eval.call_args
        assert kwargs["menu_id"] == UUID(sugg_id)
        assert kwargs["menu_date"] == test_date
        mock_eval.reset_mock()

        # 3. Log mode: evaluation IS scheduled with menu_id
        log_gemini_res = GeminiResult(
            data={
                "meal_type": "dinner",
                "items": [
                    {
                        "name": "Кефір",
                        "dish_id": None,
                        "grams": 250,
                        "kcal": 125.0,
                        "protein_g": 7.5,
                        "fat_g": 6.25,
                        "carbs_g": 10.0,
                    }
                ],
            },
            raw_text="{}",
            model="gemini-2.5-flash",
            input_tokens=100,
            output_tokens=50,
        )
        with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_gemini:
            mock_gemini.return_value = log_gemini_res
            log_resp = await client.post(
                "/nutrition/suggestions",
                json={
                    "mode": "log",
                    "text": "випив кефір",
                    "date": test_date.isoformat(),
                    "at": test_at.isoformat(),
                },
            )
        assert log_resp.status_code == 200
        logged_id = log_resp.json()["menu"]["id"]
        assert mock_eval.call_count == 1
        _, kwargs = mock_eval.call_args
        assert kwargs["menu_id"] == UUID(logged_id)
        assert kwargs["menu_date"] == test_date
        mock_eval.reset_mock()

        # 4. Grams change on eaten meal item: evaluation IS scheduled with menu_id
        item_id = eat_resp.json()["items"][0]["id"]
        patch_item_resp = await client.patch(
            f"/nutrition/menu-items/{item_id}",
            json={"portion_g": 250},
        )
        assert patch_item_resp.status_code == 200
        assert mock_eval.call_count == 1
        _, kwargs = mock_eval.call_args
        assert kwargs["menu_id"] == UUID(sugg_id)
        assert kwargs["menu_date"] == test_date
        mock_eval.reset_mock()

        # 5. Delete eaten meal: evaluation IS scheduled with menu_id=None
        del_resp = await client.delete(f"/nutrition/menus/{logged_id}")
        assert del_resp.status_code == 204
        assert mock_eval.call_count == 1
        _, kwargs = mock_eval.call_args
        assert kwargs["menu_id"] is None
        assert kwargs["menu_date"] == test_date
        mock_eval.reset_mock()

        # 6. Pinned dish materialization on GET /journal: evaluation IS scheduled with menu_id
        dish_resp = await client.post(
            "/nutrition/dishes",
            json={"name": "Каша", "is_ready": True, "pinned_meal_type": "breakfast", "kcal": 150.0},
        )
        assert dish_resp.status_code == 201
        journal_resp = await client.get(f"/nutrition/journal?date={test_date.isoformat()}")
        assert journal_resp.status_code == 200
        assert mock_eval.call_count == 1
        _, kwargs = mock_eval.call_args
        assert kwargs["menu_id"] is not None
        assert kwargs["menu_date"] == test_date
        mock_eval.reset_mock()

        # 7. POST /nutrition/menus (when created menu is eaten): evaluation IS scheduled
        create_eaten_resp = await client.post(
            "/nutrition/menus",
            json={
                "date": test_date.isoformat(),
                "meal_type": "morning_snack",
                "state": "eaten",
                "origin": "manual",
                "eaten_at": test_at.isoformat(),
            },
        )
        assert create_eaten_resp.status_code == 201
        created_eaten_id = create_eaten_resp.json()["id"]
        assert mock_eval.call_count == 1
        _, kwargs = mock_eval.call_args
        assert kwargs["menu_id"] == UUID(created_eaten_id)
        assert kwargs["menu_date"] == test_date
        mock_eval.reset_mock()

        # 8. POST /nutrition/menus/{menu_id}/items (when menu is eaten): evaluation IS scheduled
        add_item_resp = await client.post(
            f"/nutrition/menus/{created_eaten_id}/items",
            json={
                "dish_id": dish_resp.json()["id"],
                "portion_g": 100.0,
            },
        )
        assert add_item_resp.status_code == 201
        assert mock_eval.call_count == 1
        _, kwargs = mock_eval.call_args
        assert kwargs["menu_id"] == UUID(created_eaten_id)
        assert kwargs["menu_date"] == test_date


@pytest.mark.asyncio
async def test_materialize_pinned_dish_portion_cooked_weight(
    client: AsyncClient, session: AsyncSession, test_user_id: UUID
):
    test_date = date(2026, 11, 4)

    # 1. Product for composition
    p = (
        await client.post(
            "/nutrition/products", json={"name": "Гречка", "available": True}
        )
    ).json()

    # 2. Dish A: has components (100g) AND cooked_weight_g=280 -> portion_g should be 280
    dish_a = (
        await client.post(
            "/nutrition/dishes",
            json={
                "name": "Гречана каша зварена",
                "is_ready": True,
                "pinned_meal_type": "breakfast",
                "cooked_weight_g": 280,
                "kcal": 120.0,
                "protein_g": 4.0,
                "fat_g": 2.0,
                "carbs_g": 20.0,
                "components": [{"product_id": p["id"], "weight_g": 100}],
            },
        )
    ).json()

    # 3. Dish B: has components (150g) but NO cooked_weight_g -> portion_g is composition sum
    dish_b = (
        await client.post(
            "/nutrition/dishes",
            json={
                "name": "Гречка без вказаної ваги",
                "is_ready": True,
                "pinned_meal_type": "lunch",
                "kcal": 150.0,
                "protein_g": 5.0,
                "fat_g": 2.5,
                "carbs_g": 25.0,
                "components": [{"product_id": p["id"], "weight_g": 150}],
            },
        )
    ).json()

    # 4. Dish C: NO components and NO cooked_weight_g -> portion_g should default to 100
    dish_c = (
        await client.post(
            "/nutrition/dishes",
            json={
                "name": "Яблуко печене",
                "is_ready": True,
                "pinned_meal_type": "dinner",
                "kcal": 60.0,
                "protein_g": 0.5,
                "fat_g": 0.5,
                "carbs_g": 15.0,
            },
        )
    ).json()

    # Materialize via GET /journal
    j = (await client.get(f"/nutrition/journal?date={test_date.isoformat()}")).json()
    meals_by_type = {m["meal_type"]: m for m in j["meals"]}

    # Dish A: portion equals cooked_weight_g (280)
    meal_a = meals_by_type["breakfast"]
    assert meal_a["pinned_dish_id"] == dish_a["id"]
    assert meal_a["items"][0]["portion_g"] == 280
    assert meal_a["items"][0]["portion_text"] == "280 г"
    assert meal_a["items"][0]["kcal"] == round(120.0 * 2.8, 1)

    # Dish B: portion equals composition sum (150)
    meal_b = meals_by_type["lunch"]
    assert meal_b["pinned_dish_id"] == dish_b["id"]
    assert meal_b["items"][0]["portion_g"] == 150
    assert meal_b["items"][0]["portion_text"] == "150 г"
    assert meal_b["items"][0]["kcal"] == round(150.0 * 1.5, 1)

    # Dish C: portion equals 100
    meal_c = meals_by_type["dinner"]
    assert meal_c["pinned_dish_id"] == dish_c["id"]
    assert meal_c["items"][0]["portion_g"] == 100
    assert meal_c["items"][0]["portion_text"] == "100 г"
    assert meal_c["items"][0]["kcal"] == 60.0


@pytest.mark.asyncio
async def test_suggest_mode_with_eaten_text_returns_suggestion_and_writes_nothing_eaten(
    client: AsyncClient, session: AsyncSession, test_user_id: UUID
):
    """Suggest mode with text describing eaten food returns a suggestion
    and writes nothing eaten.
    """
    today = utc_today()
    test_at = datetime(today.year, today.month, today.day, 12, 0, 0, tzinfo=UTC)

    mock_gemini_res = GeminiResult(
        data={
            "meal_type": "lunch",
            "items": [
                {
                    "name": "Гречка",
                    "dish_id": None,
                    "grams": 200,
                    "kcal": 240.0,
                    "protein_g": 8.0,
                    "fat_g": 2.0,
                    "carbs_g": 46.0,
                }
            ],
        },
        raw_text="{}",
        model="gemini-2.5-flash",
        input_tokens=100,
        output_tokens=50,
    )

    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_gemini:
        mock_gemini.return_value = mock_gemini_res
        resp = await client.post(
            "/nutrition/suggestions",
            json={
                "mode": "suggest",
                "text": "з'їв гречку 200 г",
                "date": today.isoformat(),
                "at": test_at.isoformat(),
            },
        )
        assert mock_gemini.call_count == 1
        prompt = mock_gemini.call_args.kwargs["prompt"]
        assert "Тільки пропонуй прийом їжі і ніколи не записуй нічого як з'їдене" in prompt
        assert "1. ВИЗНАЧЕННЯ НАМІРУ (intent)" not in prompt
        schema = mock_gemini.call_args.kwargs["response_schema"]
        assert "intent" not in schema["properties"]
        assert "intent" not in schema["required"]

    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "suggest"
    menu = body["menu"]
    assert menu["state"] == "suggestion"
    assert menu["origin"] == "suggestion"
    assert menu["items"][0]["origin"] == "generated"

    # Journal check: no eaten meals written, suggestion is present
    j = (await client.get(f"/nutrition/journal?date={today.isoformat()}")).json()
    assert len(j["meals"]) == 0
    assert j["suggestion"] is not None
    assert j["suggestion"]["id"] == menu["id"]


@pytest.mark.asyncio
async def test_log_mode_with_eaten_text_records_eaten_meal_and_creates_no_suggestion(
    client: AsyncClient, session: AsyncSession, test_user_id: UUID
):
    """Log mode with 'з\\'їв гречку 200 г' records an eaten meal with origin manual
    and creates no suggestion.
    """
    today = utc_today()
    test_at = datetime(today.year, today.month, today.day, 12, 0, 0, tzinfo=UTC)

    mock_gemini_res = GeminiResult(
        data={
            "meal_type": "lunch",
            "items": [
                {
                    "name": "Гречка",
                    "dish_id": None,
                    "grams": 200,
                    "kcal": 240.0,
                    "protein_g": 8.0,
                    "fat_g": 2.0,
                    "carbs_g": 46.0,
                }
            ],
        },
        raw_text="{}",
        model="gemini-2.5-flash",
        input_tokens=100,
        output_tokens=50,
    )

    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_gemini:
        mock_gemini.return_value = mock_gemini_res
        resp = await client.post(
            "/nutrition/suggestions",
            json={
                "mode": "log",
                "text": "з'їв гречку 200 г",
                "date": today.isoformat(),
                "at": test_at.isoformat(),
            },
        )
        assert mock_gemini.call_count == 1
        prompt = mock_gemini.call_args.kwargs["prompt"]
        assert "Текст користувача описує їжу, яку він вже з'їв" in prompt
        assert "1. ВИЗНАЧЕННЯ НАМІРУ (intent)" not in prompt

    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "log"
    menu = body["menu"]
    assert menu["state"] == "eaten"
    assert menu["origin"] == "manual"
    assert datetime.fromisoformat(menu["eaten_at"]) == test_at
    assert menu["items"][0]["origin"] == "manual"

    # Journal check: 1 eaten meal, NO suggestion
    j = (await client.get(f"/nutrition/journal?date={today.isoformat()}")).json()
    assert len(j["meals"]) == 1
    assert j["meals"][0]["id"] == menu["id"]
    assert j["suggestion"] is None


@pytest.mark.asyncio
async def test_log_mode_past_date_lands_on_midday(
    client: AsyncClient, session: AsyncSession, test_user_id: UUID
):
    """Log into a past date lands on that date with midday time (12:00)."""
    past_date = utc_today() - timedelta(days=2)
    test_at = datetime.now(UTC)

    mock_gemini_res = GeminiResult(
        data={
            "meal_type": "lunch",
            "items": [
                {
                    "name": "Гречка",
                    "dish_id": None,
                    "grams": 200,
                    "kcal": 240.0,
                    "protein_g": 8.0,
                    "fat_g": 2.0,
                    "carbs_g": 46.0,
                }
            ],
        },
        raw_text="{}",
        model="gemini-2.5-flash",
        input_tokens=100,
        output_tokens=50,
    )

    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_gemini:
        mock_gemini.return_value = mock_gemini_res
        resp = await client.post(
            "/nutrition/suggestions",
            json={
                "mode": "log",
                "text": "з'їв гречку 200 г",
                "date": past_date.isoformat(),
                "at": test_at.isoformat(),
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "log"
    menu = body["menu"]
    assert menu["state"] == "eaten"
    assert menu["origin"] == "manual"
    assert menu["date"] == past_date.isoformat()
    eaten_at = datetime.fromisoformat(menu["eaten_at"])
    assert eaten_at == datetime(
        past_date.year, past_date.month, past_date.day, 12, 0, 0, tzinfo=UTC
    )

    # Journal for past date check
    j = (await client.get(f"/nutrition/journal?date={past_date.isoformat()}")).json()
    assert len(j["meals"]) == 1
    assert j["meals"][0]["id"] == menu["id"]
    assert j["suggestion"] is None


@pytest.mark.asyncio
async def test_log_mode_past_date_with_named_time_uses_that_time(
    client: AsyncClient, session: AsyncSession, test_user_id: UUID
):
    """Log into a past date with a named time uses that time."""
    past_date = utc_today() - timedelta(days=2)
    test_at = datetime.now(UTC)

    mock_gemini_res = GeminiResult(
        data={
            "meal_type": "dinner",
            "items": [
                {
                    "name": "Гречка",
                    "dish_id": None,
                    "grams": 200,
                    "kcal": 240.0,
                    "protein_g": 8.0,
                    "fat_g": 2.0,
                    "carbs_g": 46.0,
                }
            ],
        },
        raw_text="{}",
        model="gemini-2.5-flash",
        input_tokens=100,
        output_tokens=50,
    )

    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_gemini:
        mock_gemini.return_value = mock_gemini_res
        resp = await client.post(
            "/nutrition/suggestions",
            json={
                "mode": "log",
                "text": "з'їв гречку 200 г о 14:30",
                "date": past_date.isoformat(),
                "at": test_at.isoformat(),
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "log"
    menu = body["menu"]
    assert menu["state"] == "eaten"
    assert menu["origin"] == "manual"
    assert menu["date"] == past_date.isoformat()
    eaten_at = datetime.fromisoformat(menu["eaten_at"])
    assert eaten_at == datetime(
        past_date.year, past_date.month, past_date.day, 14, 30, 0, tzinfo=UTC
    )

    # Journal for past date check
    j = (await client.get(f"/nutrition/journal?date={past_date.isoformat()}")).json()
    assert len(j["meals"]) == 1
    assert j["meals"][0]["id"] == menu["id"]
    assert j["suggestion"] is None


@pytest.mark.asyncio
async def test_log_mode_keeps_at_when_date_equals_at_date_even_if_utc_date_differs(
    client: AsyncClient, session: AsyncSession, test_user_id: UUID
):
    """Log with date equal to at.date() keeps eaten_at == at even when
    that date differs from the UTC date (e.g. at=2026-11-04T00:30+02:00, date=2026-11-04).
    """
    test_tz = timezone(timedelta(hours=2))
    test_at = datetime(2026, 11, 4, 0, 30, 0, tzinfo=test_tz)
    test_date = date(2026, 11, 4)
    # Confirm premise: client date is 2026-11-04, but UTC date is 2026-11-03
    assert test_at.date() == test_date
    assert test_at.astimezone(UTC).date() == date(2026, 11, 3)

    mock_gemini_res = GeminiResult(
        data={
            "meal_type": "breakfast",
            "items": [
                {
                    "name": "Вівсянка",
                    "dish_id": None,
                    "grams": 200,
                    "kcal": 200.0,
                    "protein_g": 6.0,
                    "fat_g": 3.0,
                    "carbs_g": 35.0,
                }
            ],
        },
        raw_text="{}",
        model="gemini-2.5-flash",
        input_tokens=100,
        output_tokens=50,
    )

    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_gemini:
        mock_gemini.return_value = mock_gemini_res
        resp = await client.post(
            "/nutrition/suggestions",
            json={
                "mode": "log",
                "text": "вівсянка 200 г",
                "date": test_date.isoformat(),
                "at": test_at.isoformat(),
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "log"
    menu = body["menu"]
    assert menu["state"] == "eaten"
    assert menu["origin"] == "manual"
    assert menu["date"] == test_date.isoformat()
    assert datetime.fromisoformat(menu["eaten_at"]) == test_at

    j = (await client.get(f"/nutrition/journal?date={test_date.isoformat()}")).json()
    assert len(j["meals"]) == 1
    assert datetime.fromisoformat(j["meals"][0]["eaten_at"]) == test_at
    assert j["suggestion"] is None


