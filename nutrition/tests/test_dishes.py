from datetime import date

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_constant_dish_with_components(client: AsyncClient):
    payload = {
        "name": "Ранкова вівсянка",
        "kind": "constant",
        "fixed_slot": "Сніданок",
        "notes": "Базова страва",
        "components": [
            {"name": "Вівсяні пластівці", "raw_weight_g": 45},
            {"name": "Яблуко", "raw_weight_g": 100},
            {"name": "Сир кисломолочний 5%", "cooked_weight_g": 150},
        ],
    }
    resp = await client.post("/nutrition/dishes", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    dish_id = data["id"]
    assert data["name"] == "Ранкова вівсянка"
    assert data["kind"] == "constant"
    assert data["fixed_slot"] == "Сніданок"
    assert len(data["components"]) == 3
    comp_names = {c["name"] for c in data["components"]}
    assert comp_names == {"Вівсяні пластівці", "Яблуко", "Сир кисломолочний 5%"}

    # Fetch by ID
    get_resp = await client.get(f"/nutrition/dishes/{dish_id}")
    assert get_resp.status_code == 200
    assert get_resp.json() == data


@pytest.mark.asyncio
async def test_create_ready_dish(client: AsyncClient):
    payload = {
        "name": "Тушкована індичка з овочами",
        "kind": "ready",
        "cooked_weight_g": 600,
        "cooked_on": "2026-10-20",
        "components": [
            {"name": "Індичка", "raw_weight_g": 400},
            {"name": "Кабачок", "raw_weight_g": 250},
        ],
    }
    resp = await client.post("/nutrition/dishes", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Тушкована індичка з овочами"
    assert data["kind"] == "ready"
    assert data["cooked_weight_g"] == 600
    # remaining_g defaults to cooked_weight_g
    assert data["remaining_g"] == 600
    assert data["cooked_on"] == "2026-10-20"
    assert len(data["components"]) == 2


@pytest.mark.asyncio
async def test_create_ready_dish_without_weight(client: AsyncClient):
    # Ready dish without weight is allowed
    payload = {
        "name": "Кавун нарізаний",
        "kind": "ready",
    }
    resp = await client.post("/nutrition/dishes", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Кавун нарізаний"
    assert data["cooked_weight_g"] is None
    assert data["remaining_g"] is None


@pytest.mark.asyncio
async def test_dish_validation():
    from nutrition.models import DishCreate, DishUpdate

    # Constant dish without components raises ValueError
    with pytest.raises(ValueError, match="components are required for constant dishes"):
        DishCreate(name="Салат", kind="constant", components=[])

    # Negative remaining_g raises ValueError
    with pytest.raises(ValueError, match="remaining_g cannot be negative"):
        DishCreate(name="Риба", kind="ready", cooked_weight_g=300, remaining_g=-50)

    # DishUpdate validates negative remaining_g
    with pytest.raises(ValueError, match="remaining_g cannot be negative"):
        DishUpdate(remaining_g=-10)


@pytest.mark.asyncio
async def test_dish_filtering_kind_and_availability(client: AsyncClient):
    # 1. Constant dish
    await client.post(
        "/nutrition/dishes",
        json={
            "name": "Каша",
            "kind": "constant",
            "components": [{"name": "Крупа", "raw_weight_g": 50}],
        },
    )
    # 2. Ready dish with remaining > 0
    await client.post(
        "/nutrition/dishes",
        json={
            "name": "Борщ",
            "kind": "ready",
            "cooked_weight_g": 800,
            "remaining_g": 400,
            "cooked_on": str(date.today()),
        },
    )
    # 3. Ready dish with remaining = 0
    await client.post(
        "/nutrition/dishes",
        json={
            "name": "Плов",
            "kind": "ready",
            "cooked_weight_g": 500,
            "remaining_g": 0,
            "cooked_on": str(date.today()),
        },
    )

    # Filter available: should return constant dish and ready with remaining > 0
    resp_avail = await client.get("/nutrition/dishes?available=true")
    assert resp_avail.status_code == 200
    avail_names = {d["name"] for d in resp_avail.json()}
    assert "Каша" in avail_names
    assert "Борщ" in avail_names
    assert "Плов" not in avail_names


@pytest.mark.asyncio
async def test_update_and_delete_dish(client: AsyncClient):
    create_resp = await client.post(
        "/nutrition/dishes",
        json={
            "name": "Курка запечена",
            "kind": "ready",
            "cooked_weight_g": 800,
            "cooked_on": "2026-10-23",
            "components": [{"name": "Курка", "raw_weight_g": 1000}],
        },
    )
    dish_id = create_resp.json()["id"]

    # Update dish notes, remaining_g and replace components
    patch_resp = await client.patch(
        f"/nutrition/dishes/{dish_id}",
        json={
            "remaining_g": 400,
            "notes": "Половину з'їли",
            "components": [{"name": "Курка тушка", "raw_weight_g": 1000}],
        },
    )
    assert patch_resp.status_code == 200
    data = patch_resp.json()
    assert data["remaining_g"] == 400
    assert data["notes"] == "Половину з'їли"
    assert len(data["components"]) == 1
    assert data["components"][0]["name"] == "Курка тушка"

    # Delete dish
    del_resp = await client.delete(f"/nutrition/dishes/{dish_id}")
    assert del_resp.status_code == 204

    # Confirm 404
    get_resp = await client.get(f"/nutrition/dishes/{dish_id}")
    assert get_resp.status_code == 404


@pytest.mark.asyncio
async def test_dish_user_isolation(client_factory, make_user):
    user_a = await make_user("user_a@example.com")
    user_b = await make_user("user_b@example.com")

    client1 = client_factory(user_a)

    # User 1 creates dish
    resp1 = await client1.post(
        "/nutrition/dishes",
        json={
            "name": "Омлет",
            "kind": "constant",
            "components": [{"name": "Яйця", "raw_weight_g": 120}],
        },
    )
    assert resp1.status_code == 201
    dish_id = resp1.json()["id"]

    client2 = client_factory(user_b)

    # User 2 cannot access user 1's dish
    get_resp = await client2.get(f"/nutrition/dishes/{dish_id}")
    assert get_resp.status_code == 404

    # User 2 cannot update user 1's dish
    patch_resp = await client2.patch(
        f"/nutrition/dishes/{dish_id}",
        json={"name": "Хакнутий омлет"},
    )
    assert patch_resp.status_code == 404

    # User 2 cannot delete user 1's dish
    del_resp = await client2.delete(f"/nutrition/dishes/{dish_id}")
    assert del_resp.status_code == 404

    # User 1 dish still exists
    client1_again = client_factory(user_a)
    check_resp = await client1_again.get(f"/nutrition/dishes/{dish_id}")
    assert check_resp.status_code == 200
    assert check_resp.json()["name"] == "Омлет"
