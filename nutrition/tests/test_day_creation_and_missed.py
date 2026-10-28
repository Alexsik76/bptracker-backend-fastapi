from datetime import date as dt_date
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from conftest import test_session_factory as _session_factory
from gemini_infra.types import GeminiResult
from nutrition import crud
from nutrition.models import Menu, MenuDay, MenuItem


@pytest.mark.asyncio
async def test_today_auto_created_on_get(client: AsyncClient, session: AsyncSession):
    # Setup profile with 3 meals
    await client.put(
        "/nutrition/profile",
        json={
            "sex": "female",
            "birth_year": 1995,
            "height_cm": 170,
            "weight_kg": 65.0,
            "target_weight_kg": 60.0,
            "pace_kg_per_week": 0.5,
            "meals": [
                {"type": "breakfast", "time": "08:00"},
                {"type": "lunch", "time": "13:00"},
                {"type": "dinner", "time": "19:00"},
            ],
        },
    )

    # Pin breakfast
    await client.put(
        "/nutrition/pinned-meals/breakfast",
        json={
            "active": True,
            "items": [
                {"title": "Вівсянка", "portion_g": 200, "kcal": 250},
            ],
        },
    )

    today = dt_date.today()
    today_str = today.isoformat()

    # GET today auto-creates day
    resp = await client.get(f"/nutrition/menus/{today_str}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["day_created"] is True
    assert len(data["menus"]) == 3

    # Breakfast materialized from pin
    breakfast = next(m for m in data["menus"] if m["meal_type"] == "breakfast")
    assert breakfast["pinned"] is True
    assert len(breakfast["items"]) == 1
    assert breakfast["items"][0]["title"] == "Вівсянка"

    # Verify menu_days row in DB
    day_row = (await session.exec(select(MenuDay).where(MenuDay.date == today))).first()
    assert day_row is not None

    # Verify menus rows in DB
    menus_rows = (await session.exec(select(Menu).where(Menu.date == today))).all()
    assert len(menus_rows) == 3


@pytest.mark.asyncio
async def test_future_day_explicit_create_and_409_on_second_create(
    client: AsyncClient, session: AsyncSession
):
    future_date = (dt_date.today() + timedelta(days=7)).isoformat()
    future_d = dt_date.today() + timedelta(days=7)

    await client.put(
        "/nutrition/profile",
        json={
            "sex": "male",
            "birth_year": 1990,
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

    # 1. GET future uncreated day returns day_created=False and writes nothing
    get_resp = await client.get(f"/nutrition/menus/{future_date}")
    assert get_resp.status_code == 200
    assert get_resp.json()["day_created"] is False
    assert all(m["id"] is None for m in get_resp.json()["menus"])

    # DB is empty for this date
    assert (await session.exec(select(MenuDay).where(MenuDay.date == future_d))).first() is None
    assert len((await session.exec(select(Menu).where(Menu.date == future_d))).all()) == 0

    # 2. POST create creates day explicitly
    create_resp = await client.post(f"/nutrition/menus/{future_date}/create")
    assert create_resp.status_code == 201
    assert create_resp.json()["day_created"] is True
    assert all(m["id"] is not None for m in create_resp.json()["menus"])

    # DB now has rows
    assert (await session.exec(select(MenuDay).where(MenuDay.date == future_d))).first() is not None
    assert len((await session.exec(select(Menu).where(Menu.date == future_d))).all()) == 3

    # 3. Second POST create returns 409
    dup_resp = await client.post(f"/nutrition/menus/{future_date}/create")
    assert dup_resp.status_code == 409
    assert "already created" in dup_resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_read_of_uncreated_day_writes_nothing(client: AsyncClient, session: AsyncSession):
    future_date = (dt_date.today() + timedelta(days=12)).isoformat()
    future_d = dt_date.today() + timedelta(days=12)

    await client.put(
        "/nutrition/profile",
        json={
            "sex": "male",
            "birth_year": 1990,
            "height_cm": 180,
            "weight_kg": 80.0,
            "target_weight_kg": 75.0,
            "pace_kg_per_week": 0.5,
            "meals": [
                {"type": "breakfast", "time": "08:00"},
                {"type": "lunch", "time": "13:00"},
            ],
        },
    )

    # Pin lunch
    await client.put(
        "/nutrition/pinned-meals/lunch",
        json={
            "active": True,
            "items": [{"title": "Салат", "portion_g": 150}],
        },
    )

    # GET future day
    resp = await client.get(f"/nutrition/menus/{future_date}")
    assert resp.status_code == 200
    assert resp.json()["day_created"] is False

    # Verify no rows written to menu_days, menus, or menu_items for future_d
    assert (await session.exec(select(MenuDay).where(MenuDay.date == future_d))).first() is None
    assert len((await session.exec(select(Menu).where(Menu.date == future_d))).all()) == 0
    item_rows = (
        await session.exec(
            select(MenuItem).join(Menu).where(Menu.date == future_d)
        )
    ).all()
    assert len(item_rows) == 0


@pytest.mark.asyncio
async def test_operations_on_uncreated_day_return_409(client: AsyncClient):
    uncreated_date = (dt_date.today() + timedelta(days=14)).isoformat()

    await client.put(
        "/nutrition/profile",
        json={
            "sex": "female",
            "birth_year": 1990,
            "height_cm": 165,
            "weight_kg": 60.0,
            "target_weight_kg": 55.0,
            "pace_kg_per_week": 0.5,
            "meals": [
                {"type": "breakfast", "time": "08:00"},
                {"type": "lunch", "time": "13:00"},
            ],
        },
    )

    # 1. Add meal slot on uncreated day -> 409
    add_meal_resp = await client.post(
        f"/nutrition/menus/{uncreated_date}/meals",
        json={"meal_type": "afternoon_snack", "time": "16:00"},
    )
    assert add_meal_resp.status_code == 409
    assert "day not created" in add_meal_resp.json()["detail"].lower()

    # 2. Add menu item on uncreated day -> 409
    add_item_resp = await client.post(
        f"/nutrition/menus/{uncreated_date}/items?meal_type=breakfast",
        json={"title": "Омлет", "portion_g": 150},
    )
    assert add_item_resp.status_code == 409
    assert "day not created" in add_item_resp.json()["detail"].lower()

    # 3. Generate on uncreated day -> 409
    gen_resp = await client.post(
        f"/nutrition/menus/{uncreated_date}/generate",
        json={"scope": "day"},
    )
    assert gen_resp.status_code == 409
    assert "day not created" in gen_resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_missed_derivation(client: AsyncClient):
    d = (dt_date.today() + timedelta(days=2)).isoformat()

    await client.put(
        "/nutrition/profile",
        json={
            "sex": "male",
            "birth_year": 1988,
            "height_cm": 178,
            "weight_kg": 76.0,
            "target_weight_kg": 72.0,
            "pace_kg_per_week": 0.5,
            "meals": [
                {"type": "breakfast", "time": "08:00"},
                {"type": "lunch", "time": "13:00"},
                {"type": "dinner", "time": "19:00"},
            ],
        },
    )
    create_resp = await client.post(f"/nutrition/menus/{d}/create")
    assert create_resp.status_code == 201

    # Scenario A: Past day -> all uneaten meals are missed
    past_day_now = f"{(dt_date.today() + timedelta(days=3)).isoformat()}T10:00:00Z"
    resp_a = await client.get(f"/nutrition/menus/{d}?now={past_day_now}")
    menus_a = {m["meal_type"]: m for m in resp_a.json()["menus"]}
    assert menus_a["breakfast"]["status"] == "missed"
    assert menus_a["lunch"]["status"] == "missed"
    assert menus_a["dinner"]["status"] == "missed"

    # Scenario B: Today past time -> meal with time < now.time is missed, later is planned
    same_day_now = f"{d}T12:00:00Z"
    resp_b = await client.get(f"/nutrition/menus/{d}?now={same_day_now}")
    menus_b = {m["meal_type"]: m for m in resp_b.json()["menus"]}
    assert menus_b["breakfast"]["status"] == "missed"  # 08:00 < 12:00
    assert menus_b["lunch"]["status"] == "planned"    # 13:00 > 12:00
    assert menus_b["dinner"]["status"] == "planned"   # 19:00 > 12:00

    # Scenario C: Later meal eaten -> earlier meals with time < eaten_meal.time are missed
    dinner_id = menus_b["dinner"]["id"]
    mark_dinner = await client.patch(
        f"/nutrition/menus/{dinner_id}",
        json={"eaten_at": f"{d}T19:30:00Z"},
    )
    assert mark_dinner.status_code == 200

    early_now = f"{d}T07:00:00Z"  # Early morning, but Dinner is eaten!
    resp_c = await client.get(f"/nutrition/menus/{d}?now={early_now}")
    menus_c = {m["meal_type"]: m for m in resp_c.json()["menus"]}
    assert menus_c["breakfast"]["status"] == "missed"  # 08:00 < 19:00
    assert menus_c["lunch"]["status"] == "missed"      # 13:00 < 19:00
    assert menus_c["dinner"]["status"] == "eaten"


@pytest.mark.asyncio
async def test_reserve_excludes_missed_meals(client: AsyncClient):
    prod = (
        await client.post(
            "/nutrition/products",
            json={"name": "Гречка", "stock_g": 500},
        )
    ).json()
    prod_id = prod["id"]

    dish = (
        await client.post(
            "/nutrition/dishes",
            json={
                "name": "Гречана каша",
                "kind": "constant",
                "components": [{"product_id": prod_id, "weight_g": 100}],
            },
        )
    ).json()

    d = (dt_date.today() + timedelta(days=2)).isoformat()
    await client.put(
        "/nutrition/profile",
        json={
            "sex": "male",
            "birth_year": 1988,
            "height_cm": 178,
            "weight_kg": 76.0,
            "target_weight_kg": 72.0,
            "pace_kg_per_week": 0.5,
            "meals": [
                {"type": "breakfast", "time": "08:00"},
                {"type": "lunch", "time": "13:00"},
            ],
        },
    )
    await client.post(f"/nutrition/menus/{d}/create")

    # Add item to lunch (100g component)
    await client.post(
        f"/nutrition/menus/{d}/items?meal_type=lunch",
        json={"dish_id": dish["id"], "portion_g": 200},
    )

    # When now is early morning on date d -> lunch is planned, so reserves 100g
    early_now = datetime.fromisoformat(f"{d}T08:00:00+00:00")
    async with _session_factory() as s:
        stmt = select(Menu.user_id).where(Menu.date == dt_date.fromisoformat(d))
        user_id = (await s.exec(stmt)).first()
        assert user_id is not None

        reserves_early = await crud.get_product_reserves_map(s, user_id, now=early_now)
        assert reserves_early.get(UUID(prod_id), 0) == 100

        # When now is afternoon on date d (15:00 > 13:00) -> lunch is missed, so reserves 0g
        late_now = datetime.fromisoformat(f"{d}T15:00:00+00:00")
        reserves_late = await crud.get_product_reserves_map(s, user_id, now=late_now)
        assert reserves_late.get(UUID(prod_id), 0) == 0


@pytest.mark.asyncio
async def test_generation_skips_missed_and_pinned_meals(client: AsyncClient):
    d = (dt_date.today() + timedelta(days=2)).isoformat()
    await client.put(
        "/nutrition/profile",
        json={
            "sex": "male",
            "birth_year": 1988,
            "height_cm": 178,
            "weight_kg": 76.0,
            "target_weight_kg": 72.0,
            "pace_kg_per_week": 0.5,
            "meals": [
                {"type": "breakfast", "time": "08:00"},
                {"type": "lunch", "time": "13:00"},
                {"type": "dinner", "time": "19:00"},
            ],
        },
    )

    dish = (
        await client.post(
            "/nutrition/dishes",
            json={
                "name": "Легка вечеря",
                "kind": "ready",
                "cooked_weight_g": 500,
                "cooked_on": d,
            },
        )
    ).json()

    # Active pinned meal for Lunch
    await client.put(
        "/nutrition/pinned-meals/lunch",
        json={
            "active": True,
            "items": [{"title": "Закріплений обід", "portion_g": 250, "kcal": 400}],
        },
    )

    await client.post(f"/nutrition/menus/{d}/create")

    # Now is 10:00 on date d:
    # - Breakfast (08:00) is missed
    # - Lunch (13:00) is pinned
    # - Dinner (19:00) is generatable
    mock_gemini = GeminiResult(
        data={
            "rationale": "Вечеря згенерована",
            "shopping_list": [],
            "items": [
                {
                    "meal_type": "dinner",
                    "dish_id": dish["id"],
                    "title": "Легка вечеря",
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

    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_gen:
        mock_gen.return_value = mock_gemini
        gen_resp = await client.post(
            f"/nutrition/menus/{d}/generate",
            json={"scope": "day", "now": f"{d}T10:00:00Z"},
        )
        assert gen_resp.status_code == 200
        result = gen_resp.json()
        menus_by_type = {m["meal_type"]: m for m in result["menus"]}
        # Breakfast: missed, untouched / no items
        assert menus_by_type["breakfast"]["items"] == []
        # Lunch: pinned, untouched
        assert len(menus_by_type["lunch"]["items"]) == 1
        assert menus_by_type["lunch"]["items"][0]["title"] == "Закріплений обід"
        # Dinner: generated
        assert len(menus_by_type["dinner"]["items"]) == 1
        assert menus_by_type["dinner"]["items"][0]["title"] == "Легка вечеря"

        # Verify prompt passed to gemini
        call_kwargs = mock_gen.call_args[1]
        prompt_text = call_kwargs["prompt"]
        # Breakfast passed as missed / not eaten
        assert "breakfast" in prompt_text.lower()
        # Lunch pinned passed as fixed fact
        assert "lunch" in prompt_text.lower()
