import json
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from config import get_settings
from gemini_infra.types import GeminiInvalidResponse, GeminiResult, GeminiUnavailable
from nutrition import crud
from nutrition.models import (
    DayMenuRead,
    GenerationLog,
    KcalSource,
    MenuItem,
    MenuItemCreate,
    MenuItemUpdate,
    MenuUpdate,
)
from nutrition.service.estimator import (
    ESTIMATE_PROMPT_VERSION,
    estimate_day_kcal,
)
from nutrition.service.generator import (
    ServiceGenerationInvalidResponse,
)


@pytest.mark.asyncio
async def test_estimate_stores_model_values(
    client: AsyncClient, session: AsyncSession, test_user_id: UUID
):
    settings = get_settings()
    test_date = date(2026, 10, 27)
    user_id = test_user_id

    # 1. Create a ready dish
    dish = (
        await client.post(
            "/nutrition/dishes",
            json={
                "name": "Гречка відварна",
                "kind": "ready",
                "cooked_weight_g": 500,
                "cooked_on": "2026-10-27",
            },
        )
    ).json()

    # 2. Add dish item without kcal_est
    item1 = await crud.add_menu_item(
        session,
        user_id,
        test_date,
        "Сніданок",
        MenuItemCreate(
            dish_id=UUID(dish["id"]),
            portion_g=200,
            portion_text="200 г",
        ),
    )

    # 3. Add free-text item without kcal_est
    item2 = await crud.add_menu_item(
        session,
        user_id,
        test_date,
        "Сніданок",
        MenuItemCreate(
            title="Огірок свіжий",
            portion_g=100,
            portion_text="1 шт",
        ),
    )

    # 4. Add user-provided kcal item
    item3 = await crud.add_menu_item(
        session,
        user_id,
        test_date,
        "Сніданок",
        MenuItemCreate(
            title="Кава чорна",
            portion_text="1 чашка",
            kcal_est=5,
        ),
    )

    # 5. Add item to Lunch, mark Lunch eaten without kcal_est
    item4 = await crud.add_menu_item(
        session,
        user_id,
        test_date,
        "Обід",
        MenuItemCreate(
            title="Хліб",
            portion_g=50,
        ),
    )
    lunch_menu = await crud.get_menu(session, user_id, test_date, "Обід")
    assert lunch_menu is not None and lunch_menu.id is not None
    await crud.update_menu(
        session,
        lunch_menu.id,
        user_id,
        MenuUpdate(eaten_at=datetime.now(UTC)),
    )

    mock_gemini = GeminiResult(
        data={
            "items": [
                {"id": str(item1.id), "kcal_est": 220},
                {"id": str(item2.id), "kcal_est": 15},
            ]
        },
        raw_text=json.dumps(
            {
                "items": [
                    {"id": str(item1.id), "kcal_est": 220},
                    {"id": str(item2.id), "kcal_est": 15},
                ]
            }
        ),
        model=settings.gemini_menu_model,
        input_tokens=150,
        output_tokens=60,
    )

    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_gen:
        mock_gen.return_value = mock_gemini
        day_menu = await estimate_day_kcal(
            session=session,
            user_id=user_id,
            menu_date=test_date,
            settings=settings,
        )

        assert mock_gen.call_count == 1
        call_kwargs = mock_gen.call_args.kwargs
        prompt_text = call_kwargs["prompt"]
        assert "Гречка відварна" in prompt_text
        assert "Огірок свіжий" in prompt_text
        # Item 3 and Item 4 shouldn't be in the prompt to estimate
        assert str(item3.id) not in prompt_text
        assert str(item4.id) not in prompt_text

    # Verify return day_menu items
    items_by_id = {it.id: it for m in day_menu.menus for it in m.items}
    assert items_by_id[item1.id].kcal_est == 220
    assert items_by_id[item1.id].kcal_source == KcalSource.MODEL

    assert items_by_id[item2.id].kcal_est == 15
    assert items_by_id[item2.id].kcal_source == KcalSource.MODEL

    assert items_by_id[item3.id].kcal_est == 5
    assert items_by_id[item3.id].kcal_source == KcalSource.USER

    assert items_by_id[item4.id].kcal_est is None
    assert items_by_id[item4.id].kcal_source is None

    # Check generation_log
    log_stmt = select(GenerationLog).where(
        GenerationLog.user_id == user_id,
        GenerationLog.date == test_date,
        GenerationLog.scope == "estimate",
    )
    logs = list((await session.exec(log_stmt)).all())
    assert len(logs) == 1
    assert logs[0].prompt_version == ESTIMATE_PROMPT_VERSION
    assert logs[0].input_tokens == 150
    assert logs[0].output_tokens == 60


@pytest.mark.asyncio
async def test_nothing_to_estimate_makes_no_gemini_call(
    session: AsyncSession, test_user_id: UUID
):
    settings = get_settings()
    test_date = date(2026, 10, 28)
    user_id = test_user_id

    # Item with kcal_est already populated
    await crud.add_menu_item(
        session,
        user_id,
        test_date,
        "Обід",
        MenuItemCreate(
            title="Суп",
            portion_g=300,
            kcal_est=180,
        ),
    )
    # Item with kcal_est already populated
    await crud.add_menu_item(
        session,
        user_id,
        test_date,
        "Обід",
        MenuItemCreate(
            title="Десерт",
            portion_g=100,
            kcal_est=250,
        ),
    )

    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_gen:
        day_menu = await estimate_day_kcal(
            session=session,
            user_id=user_id,
            menu_date=test_date,
            settings=settings,
        )
        assert mock_gen.call_count == 0

    assert len(day_menu.menus) == 1
    assert day_menu.kcal_incomplete is False

    log_stmt = select(GenerationLog).where(
        GenerationLog.user_id == user_id,
        GenerationLog.date == test_date,
        GenerationLog.scope == "estimate",
    )
    logs = list((await session.exec(log_stmt)).all())
    assert len(logs) == 0


@pytest.mark.asyncio
async def test_invalid_ids_rejected(session: AsyncSession, test_user_id: UUID):
    settings = get_settings()
    test_date = date(2026, 10, 29)
    user_id = test_user_id

    item = await crud.add_menu_item(
        session,
        user_id,
        test_date,
        "Вечеря",
        MenuItemCreate(
            title="Сир кисломолочний",
            portion_g=150,
        ),
    )

    # 1. Unknown ID returned by Gemini
    unknown_id = str(uuid4())
    mock_gemini = GeminiResult(
        data={"items": [{"id": unknown_id, "kcal_est": 180}]},
        raw_text="{}",
        model=settings.gemini_menu_model,
        input_tokens=100,
        output_tokens=50,
    )
    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_gen:
        mock_gen.return_value = mock_gemini
        with pytest.raises(ServiceGenerationInvalidResponse):
            await estimate_day_kcal(
                session=session,
                user_id=user_id,
                menu_date=test_date,
                settings=settings,
            )

    # 2. Duplicate IDs returned
    mock_gemini_dup = GeminiResult(
        data={
            "items": [
                {"id": str(item.id), "kcal_est": 180},
                {"id": str(item.id), "kcal_est": 180},
            ]
        },
        raw_text="{}",
        model=settings.gemini_menu_model,
        input_tokens=100,
        output_tokens=50,
    )
    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_gen:
        mock_gen.return_value = mock_gemini_dup
        with pytest.raises(ServiceGenerationInvalidResponse):
            await estimate_day_kcal(
                session=session,
                user_id=user_id,
                menu_date=test_date,
                settings=settings,
            )

    # 3. Negative kcal returned
    mock_gemini_neg = GeminiResult(
        data={"items": [{"id": str(item.id), "kcal_est": -50}]},
        raw_text="{}",
        model=settings.gemini_menu_model,
        input_tokens=100,
        output_tokens=50,
    )
    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_gen:
        mock_gen.return_value = mock_gemini_neg
        with pytest.raises(ServiceGenerationInvalidResponse):
            await estimate_day_kcal(
                session=session,
                user_id=user_id,
                menu_date=test_date,
                settings=settings,
            )

    # 4. Missing ID (empty items list)
    mock_gemini_empty = GeminiResult(
        data={"items": []},
        raw_text="{}",
        model=settings.gemini_menu_model,
        input_tokens=100,
        output_tokens=50,
    )
    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_gen:
        mock_gen.return_value = mock_gemini_empty
        with pytest.raises(ServiceGenerationInvalidResponse):
            await estimate_day_kcal(
                session=session,
                user_id=user_id,
                menu_date=test_date,
                settings=settings,
            )


@pytest.mark.asyncio
async def test_reset_after_manual_edit(session: AsyncSession, test_user_id: UUID):
    test_date = date(2026, 10, 30)
    user_id = test_user_id

    # Item with model estimate
    item = await crud.add_menu_item(
        session,
        user_id,
        test_date,
        "Обід",
        MenuItemCreate(
            title="Рагу овочеве",
            portion_g=250,
            kcal_est=180,
        ),
    )
    # Simulate that it was estimated by model
    item_db = (
        await session.exec(select(MenuItem).where(MenuItem.id == item.id))
    ).first()
    item_db.kcal_source = KcalSource.MODEL
    session.add(item_db)
    await session.commit()

    # Edit with UNCHANGED values -> keeps model estimate
    unchanged = await crud.update_menu_item(
        session,
        item.id,
        user_id,
        MenuItemUpdate(portion_g=250, title="Рагу овочеве"),
    )
    assert unchanged.kcal_est == 180
    assert unchanged.kcal_source == KcalSource.MODEL

    # Edit portion_g with CHANGED value -> resets kcal_est & kcal_source to None
    updated = await crud.update_menu_item(
        session,
        item.id,
        user_id,
        MenuItemUpdate(portion_g=300),
    )
    assert updated.kcal_est is None
    assert updated.kcal_source is None

    # Restore model estimate
    item_db = (
        await session.exec(select(MenuItem).where(MenuItem.id == item.id))
    ).first()
    item_db.kcal_est = 200
    item_db.kcal_source = KcalSource.MODEL
    session.add(item_db)
    await session.commit()

    # Edit title -> resets
    updated = await crud.update_menu_item(
        session,
        item.id,
        user_id,
        MenuItemUpdate(title="Рагу з квасолею"),
    )
    assert updated.kcal_est is None
    assert updated.kcal_source is None

    # Restore model estimate
    item_db = (
        await session.exec(select(MenuItem).where(MenuItem.id == item.id))
    ).first()
    item_db.kcal_est = 200
    item_db.kcal_source = KcalSource.MODEL
    session.add(item_db)
    await session.commit()

    # Edit position -> does NOT reset
    updated = await crud.update_menu_item(
        session,
        item.id,
        user_id,
        MenuItemUpdate(position=2),
    )
    assert updated.kcal_est == 200
    assert updated.kcal_source == KcalSource.MODEL

    # Edit portion_g AND set kcal_est -> sets user kcal
    updated = await crud.update_menu_item(
        session,
        item.id,
        user_id,
        MenuItemUpdate(portion_g=350, kcal_est=280),
    )
    assert updated.kcal_est == 280
    assert updated.kcal_source == KcalSource.USER

    # Now item has USER kcal -> editing portion_g alone keeps user kcal
    updated = await crud.update_menu_item(
        session,
        item.id,
        user_id,
        MenuItemUpdate(portion_g=400),
    )
    assert updated.kcal_est == 280
    assert updated.kcal_source == KcalSource.USER


@pytest.mark.asyncio
async def test_kcal_incomplete_flags(session: AsyncSession, test_user_id: UUID):
    test_date = date(2026, 10, 31)
    user_id = test_user_id

    # 1. Unmarked item without kcal_est -> both menu and day_menu are kcal_incomplete
    item = await crud.add_menu_item(
        session,
        user_id,
        test_date,
        "Сніданок",
        MenuItemCreate(
            title="Тост",
            portion_g=60,
        ),
    )
    day_menus = await crud.get_day_menus(session, user_id, test_date)
    assert len(day_menus) == 1
    assert day_menus[0].kcal_incomplete is True

    day_read = DayMenuRead(date=test_date, menus=day_menus)
    assert day_read.kcal_incomplete is True

    # 2. Item with kcal_est populated (e.g. by model estimation) -> kcal_incomplete is False
    item_db = (
        await session.exec(select(MenuItem).where(MenuItem.id == item.id))
    ).first()
    item_db.kcal_est = 160
    session.add(item_db)
    await session.commit()
    day_menus = await crud.get_day_menus(session, user_id, test_date)
    assert day_menus[0].kcal_incomplete is False
    day_read = DayMenuRead(date=test_date, menus=day_menus)
    assert day_read.kcal_incomplete is False


@pytest.mark.asyncio
async def test_endpoint_happy_path_and_errors(client: AsyncClient):
    date_str = "2026-11-01"

    # Create 2 items
    resp1 = await client.post(
        f"/nutrition/menus/{date_str}/items?slot=Сніданок",
        json={"title": "Млинці", "portion_g": 200},
    )
    item1_id = resp1.json()["id"]

    resp2 = await client.post(
        f"/nutrition/menus/{date_str}/items?slot=Обід",
        json={"title": "Борщ", "portion_g": 350},
    )
    item2_id = resp2.json()["id"]

    # Before estimate, day menu should be kcal_incomplete
    get_resp = await client.get(f"/nutrition/menus/{date_str}")
    assert get_resp.status_code == 200
    assert get_resp.json()["kcal_incomplete"] is True

    # 1. Gemini unavailable -> 502
    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_gen:
        mock_gen.side_effect = GeminiUnavailable("Timeout")
        est_resp = await client.post(f"/nutrition/menus/{date_str}/estimate")
        assert est_resp.status_code == 502

    # 2. Gemini invalid response -> 422
    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_gen:
        mock_gen.side_effect = GeminiInvalidResponse("Bad json", raw_text="not json")
        est_resp = await client.post(f"/nutrition/menus/{date_str}/estimate")
        assert est_resp.status_code == 422

    # 3. Happy path: estimate only one slot
    mock_gemini_slot = GeminiResult(
        data={"items": [{"id": item1_id, "kcal_est": 340}]},
        raw_text="{}",
        model="gemini-2.0-flash",
        input_tokens=100,
        output_tokens=50,
    )
    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_gen:
        mock_gen.return_value = mock_gemini_slot
        est_resp = await client.post(f"/nutrition/menus/{date_str}/estimate?slot=Сніданок")
        assert est_resp.status_code == 200
        data = est_resp.json()
        breakfast = [m for m in data["menus"] if m["slot"] == "Сніданок"][0]
        assert breakfast["items"][0]["kcal_est"] == 340
        assert breakfast["items"][0]["kcal_source"] == "model"
        assert breakfast["kcal_incomplete"] is False
        # Lunch still incomplete, so day is incomplete
        lunch = [m for m in data["menus"] if m["slot"] == "Обід"][0]
        assert lunch["kcal_incomplete"] is True
        assert data["kcal_incomplete"] is True

    # 4. Happy path: estimate remaining items for day
    mock_gemini_day = GeminiResult(
        data={"items": [{"id": item2_id, "kcal_est": 250}]},
        raw_text="{}",
        model="gemini-2.0-flash",
        input_tokens=100,
        output_tokens=50,
    )
    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_gen:
        mock_gen.return_value = mock_gemini_day
        est_resp = await client.post(f"/nutrition/menus/{date_str}/estimate")
        assert est_resp.status_code == 200
        data = est_resp.json()
        assert data["kcal_incomplete"] is False
        lunch = [m for m in data["menus"] if m["slot"] == "Обід"][0]
        assert lunch["items"][0]["kcal_est"] == 250
        assert lunch["items"][0]["kcal_source"] == "model"
        assert lunch["kcal_incomplete"] is False


@pytest.mark.asyncio
async def test_unchanged_patch_keeps_model_estimate(client: AsyncClient):
    date_str = "2026-11-02"

    # 1. Create item
    resp = await client.post(
        f"/nutrition/menus/{date_str}/items?slot=Сніданок",
        json={"title": "Вівсянка", "portion_g": 200, "portion_text": "200 г"},
    )
    item_id = resp.json()["id"]

    # 2. Estimate with Gemini
    mock_gemini = GeminiResult(
        data={"items": [{"id": item_id, "kcal_est": 210}]},
        raw_text="{}",
        model="gemini-2.0-flash",
        input_tokens=100,
        output_tokens=50,
    )
    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_gen:
        mock_gen.return_value = mock_gemini
        est_resp = await client.post(f"/nutrition/menus/{date_str}/estimate")
        assert est_resp.status_code == 200
        breakfast = [m for m in est_resp.json()["menus"] if m["slot"] == "Сніданок"][0]
        assert breakfast["items"][0]["kcal_est"] == 210
        assert breakfast["items"][0]["kcal_source"] == "model"

    # 3. PATCH simulating web client sending portion_g and portion_text unchanged
    patch_resp = await client.patch(
        f"/nutrition/menu-items/{item_id}",
        json={
            "portion_g": 200,
            "portion_text": "200 г",
            "title": "Вівсянка",
        },
    )
    assert patch_resp.status_code == 200
    patched_item = patch_resp.json()
    assert patched_item["kcal_est"] == 210
    assert patched_item["kcal_source"] == "model"

    menu_id = resp.json()["menu_id"]

    # 4. Mark meal eaten -> changing portion_g on an eaten meal is refused with 409
    await client.patch(f"/nutrition/menus/{menu_id}", json={"eaten_at": "2026-11-02T08:30:00Z"})
    refused_resp = await client.patch(
        f"/nutrition/menu-items/{item_id}",
        json={
            "portion_g": 250,
            "portion_text": "250 г",
        },
    )
    assert refused_resp.status_code == 409

    # Unmark eaten meal
    unmark_resp = await client.patch(
        f"/nutrition/menus/{menu_id}",
        json={"eaten_at": None},
    )
    assert unmark_resp.status_code == 200

    # Now change portion_g to 250 -> model estimate should reset
    change_resp = await client.patch(
        f"/nutrition/menu-items/{item_id}",
        json={
            "portion_g": 250,
            "portion_text": "250 г",
        },
    )
    assert change_resp.status_code == 200
    changed_item = change_resp.json()
    assert changed_item["kcal_est"] is None
    assert changed_item["kcal_source"] is None
