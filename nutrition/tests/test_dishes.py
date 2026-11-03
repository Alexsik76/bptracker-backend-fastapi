from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from httpx import AsyncClient

from gemini_infra import GeminiResult
from gemini_infra.types import GeminiUnavailable
from nutrition.models import DishComponentCreate, DishCreate, DishUpdate


@pytest.fixture(autouse=True)
def _mock_gemini_default():
    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as m:
        m.return_value = GeminiResult(
            data={"kcal": 120.0, "protein_g": 10.0, "fat_g": 3.0, "carbs_g": 15.0},
            raw_text='{"kcal": 120.0, "protein_g": 10.0, "fat_g": 3.0, "carbs_g": 15.0}',
            model="gemini-2.0-flash",
            input_tokens=10,
            output_tokens=10,
        )
        yield m


@pytest.mark.asyncio
async def test_create_dish_with_only_name(client: AsyncClient):
    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_gemini:
        mock_gemini.return_value = GeminiResult(
            data={
                "kcal": 215.0,
                "protein_g": 24.0,
                "fat_g": 12.0,
                "carbs_g": 0.0,
                "fiber_g": None,
                "sugar_g": None,
                "sodium_mg": 70.0,
            },
            raw_text="{}",
            model="gemini-2.0-flash",
            input_tokens=20,
            output_tokens=20,
        )
        resp = await client.post("/nutrition/dishes", json={"name": "Філе індички запечене"})
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Філе індички запечене"
        assert data["is_ready"] is False
        assert data["pinned_meal_type"] is None
        assert data["nutrition_overridden"] is False
        assert data["kcal"] == 215.0
        assert data["protein_g"] == 24.0
        assert data["fat_g"] == 12.0
        assert data["carbs_g"] == 0.0
        assert data["sodium_mg"] == 70.0
        assert mock_gemini.call_count == 1


@pytest.mark.asyncio
async def test_create_dish_with_explicit_nutrition(client: AsyncClient):
    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_gemini:
        payload = {
            "name": "Сир домашній",
            "is_ready": True,
            "pinned_meal_type": "breakfast",
            "note": "жирний",
            "kcal": 150.0,
            "protein_g": 16.0,
            "fat_g": 9.0,
            "carbs_g": 2.0,
        }
        resp = await client.post("/nutrition/dishes", json=payload)
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Сир домашній"
        assert data["is_ready"] is True
        assert data["pinned_meal_type"] == "breakfast"
        assert data["nutrition_overridden"] is True
        assert data["note"] == "жирний"
        assert data["kcal"] == 150.0
        assert mock_gemini.call_count == 0


@pytest.mark.asyncio
async def test_create_dish_with_components(client: AsyncClient):
    p1 = (
        await client.post(
            "/nutrition/products", json={"name": "Вівсяні пластівці", "available": True}
        )
    ).json()

    payload = {
        "name": "Вівсянка з яблуком",
        "is_ready": False,
        "note": "на воді",
        "components": [
            {"product_id": p1["id"], "weight_g": 50},
            {"new_product": {"name": "Яблуко", "available": True}, "weight_g": 100},
        ],
    }
    resp = await client.post("/nutrition/dishes", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Вівсянка з яблуком"
    assert len(data["components"]) == 2
    comp_product_ids = {c["product_id"] for c in data["components"]}
    assert p1["id"] in comp_product_ids


@pytest.mark.asyncio
async def test_dish_crud_and_filters(client: AsyncClient):
    d1 = (
        await client.post(
            "/nutrition/dishes",
            json={
                "name": "Омлет",
                "is_ready": True,
                "pinned_meal_type": "breakfast",
                "kcal": 180.0,
                "protein_g": 14.0,
                "fat_g": 13.0,
                "carbs_g": 2.0,
            },
        )
    ).json()
    d2 = (
        await client.post(
            "/nutrition/dishes",
            json={
                "name": "Борщ",
                "is_ready": False,
                "kcal": 60.0,
                "protein_g": 4.0,
                "fat_g": 2.5,
                "carbs_g": 6.0,
            },
        )
    ).json()

    # Get single dish
    get_resp = await client.get(f"/nutrition/dishes/{d1['id']}")
    assert get_resp.status_code == 200
    assert get_resp.json()["name"] == "Омлет"

    # List all
    all_dishes = (await client.get("/nutrition/dishes")).json()
    assert len(all_dishes) >= 2

    # Filter is_ready=true
    ready_dishes = (await client.get("/nutrition/dishes?is_ready=true")).json()
    assert any(d["id"] == d1["id"] for d in ready_dishes)
    assert not any(d["id"] == d2["id"] for d in ready_dishes)

    # Filter pinned_meal_type=breakfast
    pinned_dishes = (await client.get("/nutrition/dishes?pinned_meal_type=breakfast")).json()
    assert any(d["id"] == d1["id"] for d in pinned_dishes)
    assert not any(d["id"] == d2["id"] for d in pinned_dishes)

    # Update dish (toggle ready, override kcal)
    patch_resp = await client.patch(
        f"/nutrition/dishes/{d2['id']}",
        json={"is_ready": True, "kcal": 65.0},
    )
    assert patch_resp.status_code == 200
    patched = patch_resp.json()
    assert patched["is_ready"] is True
    assert patched["kcal"] == 65.0
    assert patched["nutrition_overridden"] is True

    # Delete dish
    del_resp = await client.delete(f"/nutrition/dishes/{d2['id']}")
    assert del_resp.status_code == 204
    assert (await client.get(f"/nutrition/dishes/{d2['id']}")).status_code == 404


@pytest.mark.asyncio
async def test_dish_user_isolation(client_factory, make_user):
    user_a = await make_user("da@example.com")
    user_b = await make_user("db@example.com")

    client_a = client_factory(user_a)
    d = (
        await client_a.post(
            "/nutrition/dishes",
            json={
                "name": "Каша гречана",
                "kcal": 110.0,
                "protein_g": 4.0,
                "fat_g": 1.0,
                "carbs_g": 20.0,
            },
        )
    ).json()

    client_b = client_factory(user_b)
    # User B cannot see or modify User A's dish
    list_b = (await client_b.get("/nutrition/dishes")).json()
    assert not any(dish["id"] == d["id"] for dish in list_b)
    assert (await client_b.get(f"/nutrition/dishes/{d['id']}")).status_code == 404
    patch_resp = await client_b.patch(f"/nutrition/dishes/{d['id']}", json={"name": "X"})
    assert patch_resp.status_code == 404
    assert (await client_b.delete(f"/nutrition/dishes/{d['id']}")).status_code == 404


@pytest.mark.asyncio
async def test_dish_component_validation():
    with pytest.raises(ValueError, match="weight_g must be > 0"):
        DishComponentCreate(product_id=uuid4(), weight_g=0)

    with pytest.raises(ValueError, match="Either product_id or new_product must be provided"):
        DishComponentCreate(weight_g=100)


@pytest.mark.asyncio
async def test_dish_cooked_weight_validation(client: AsyncClient):
    with pytest.raises(ValueError, match="cooked_weight_g must be > 0"):
        DishCreate(name="Борщ", cooked_weight_g=0)

    with pytest.raises(ValueError, match="cooked_weight_g must be > 0"):
        DishCreate(name="Борщ", cooked_weight_g=-10)

    with pytest.raises(ValueError, match="cooked_weight_g must be > 0"):
        DishUpdate(cooked_weight_g=0)

    with pytest.raises(ValueError, match="cooked_weight_g must be > 0"):
        DishUpdate(cooked_weight_g=-5)

    # Valid values
    dc = DishCreate(name="Борщ", cooked_weight_g=300)
    assert dc.cooked_weight_g == 300
    dc_none = DishCreate(name="Борщ", cooked_weight_g=None)
    assert dc_none.cooked_weight_g is None

    du = DishUpdate(cooked_weight_g=300)
    assert du.cooked_weight_g == 300
    du_none = DishUpdate(cooked_weight_g=None)
    assert du_none.cooked_weight_g is None

    # Via API: invalid cooked_weight_g returns 422
    err_resp = await client.post("/nutrition/dishes", json={"name": "Суп", "cooked_weight_g": 0})
    assert err_resp.status_code == 422


@pytest.mark.asyncio
async def test_create_dish_estimates_from_composition(
    client: AsyncClient, _mock_gemini_default: AsyncMock
):
    _mock_gemini_default.reset_mock()
    p1 = (
        await client.post(
            "/nutrition/products", json={"name": "Вівсяні пластівці", "available": True}
        )
    ).json()

    payload = {
        "name": "Вівсянка з бананом",
        "components": [
            {"product_id": p1["id"], "weight_g": 60},
            {"new_product": {"name": "Банан", "available": True}, "weight_g": 120},
        ],
    }
    resp = await client.post("/nutrition/dishes", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["kcal"] == 120.0
    assert data["nutrition_overridden"] is False

    assert _mock_gemini_default.call_count == 1
    call_prompt = _mock_gemini_default.call_args.kwargs["prompt"]
    assert "Вівсяні пластівці: 60 г" in call_prompt
    assert "Банан: 120 г" in call_prompt
    assert "180" in call_prompt


@pytest.mark.asyncio
async def test_components_change_reestimates_when_not_overridden_and_not_when_overridden(
    client: AsyncClient, _mock_gemini_default: AsyncMock
):
    p1 = (await client.post("/nutrition/products", json={"name": "Сир", "available": True})).json()
    p2 = (await client.post("/nutrition/products", json={"name": "Мед", "available": True})).json()

    # Part 1: Dish without overridden nutrition -> re-estimates on components change
    _mock_gemini_default.reset_mock()
    d1 = (
        await client.post(
            "/nutrition/dishes",
            json={
                "name": "Сир з медом",
                "components": [{"product_id": p1["id"], "weight_g": 100}],
            },
        )
    ).json()
    assert d1["nutrition_overridden"] is False
    assert _mock_gemini_default.call_count == 1

    # Update components -> triggers re-estimation with new composition
    _mock_gemini_default.reset_mock()
    patch_resp = await client.patch(
        f"/nutrition/dishes/{d1['id']}",
        json={
            "components": [
                {"product_id": p1["id"], "weight_g": 150},
                {"product_id": p2["id"], "weight_g": 20},
            ]
        },
    )
    assert patch_resp.status_code == 200
    assert _mock_gemini_default.call_count == 1
    prompt = _mock_gemini_default.call_args.kwargs["prompt"]
    assert "Сир: 150 г" in prompt
    assert "Мед: 20 г" in prompt
    assert "170" in prompt

    # Update components to empty -> re-estimates by name
    _mock_gemini_default.reset_mock()
    empty_resp = await client.patch(
        f"/nutrition/dishes/{d1['id']}",
        json={"components": []},
    )
    assert empty_resp.status_code == 200
    assert _mock_gemini_default.call_count == 1
    empty_prompt = _mock_gemini_default.call_args.kwargs["prompt"]
    assert "Склад страви" not in empty_prompt
    assert "- Сир:" not in empty_prompt

    # Part 2: Dish with overridden nutrition -> does NOT re-estimate on components change
    d2 = (
        await client.post(
            "/nutrition/dishes",
            json={
                "name": "Каша гречана з маслом",
                "kcal": 250.0,
                "protein_g": 6.0,
                "fat_g": 8.0,
                "carbs_g": 35.0,
                "components": [{"product_id": p1["id"], "weight_g": 100}],
            },
        )
    ).json()
    assert d2["nutrition_overridden"] is True

    _mock_gemini_default.reset_mock()
    patch_overridden = await client.patch(
        f"/nutrition/dishes/{d2['id']}",
        json={"components": [{"product_id": p1["id"], "weight_g": 200}]},
    )
    assert patch_overridden.status_code == 200
    assert _mock_gemini_default.call_count == 0
    updated_d2 = patch_overridden.json()
    assert updated_d2["kcal"] == 250.0
    assert updated_d2["nutrition_overridden"] is True

    # Part 3: Request that sets both components and nutrition fields
    # -> nutrition fields win, no re-estimation
    _mock_gemini_default.reset_mock()
    patch_both = await client.patch(
        f"/nutrition/dishes/{d1['id']}",
        json={
            "components": [{"product_id": p1["id"], "weight_g": 200}],
            "kcal": 300.0,
        },
    )
    assert patch_both.status_code == 200
    assert _mock_gemini_default.call_count == 0
    updated_both = patch_both.json()
    assert updated_both["kcal"] == 300.0
    assert updated_both["nutrition_overridden"] is True


@pytest.mark.asyncio
async def test_reset_nutrition_clears_flag_and_reestimates(
    client: AsyncClient, _mock_gemini_default: AsyncMock
):
    p1 = (
        await client.post("/nutrition/products", json={"name": "Гречка", "available": True})
    ).json()

    d = (
        await client.post(
            "/nutrition/dishes",
            json={
                "name": "Гречана каша",
                "kcal": 400.0,
                "protein_g": 12.0,
                "fat_g": 5.0,
                "carbs_g": 70.0,
                "components": [{"product_id": p1["id"], "weight_g": 100}],
            },
        )
    ).json()
    assert d["nutrition_overridden"] is True
    assert d["kcal"] == 400.0

    _mock_gemini_default.reset_mock()
    _mock_gemini_default.side_effect = None
    _mock_gemini_default.return_value = GeminiResult(
        data={"kcal": 135.0, "protein_g": 4.5, "fat_g": 1.2, "carbs_g": 26.0},
        raw_text='{"kcal": 135.0}',
        model="gemini-2.0-flash",
        input_tokens=10,
        output_tokens=10,
    )

    # 1. Reset nutrition on overridden dish
    reset_resp = await client.patch(
        f"/nutrition/dishes/{d['id']}",
        json={"reset_nutrition": True},
    )
    assert reset_resp.status_code == 200
    data = reset_resp.json()
    assert data["nutrition_overridden"] is False
    assert data["kcal"] == 135.0
    assert _mock_gemini_default.call_count == 1
    call_prompt = _mock_gemini_default.call_args.kwargs["prompt"]
    assert "Гречка: 100 г" in call_prompt

    # 2. Reset nutrition while supplying new nutrition fields -> nutrition fields ignored
    await client.patch(f"/nutrition/dishes/{d['id']}", json={"kcal": 999.0})
    _mock_gemini_default.reset_mock()
    reset_ignore_resp = await client.patch(
        f"/nutrition/dishes/{d['id']}",
        json={"reset_nutrition": True, "kcal": 777.0, "fat_g": 50.0},
    )
    assert reset_ignore_resp.status_code == 200
    data_ignored = reset_ignore_resp.json()
    assert data_ignored["nutrition_overridden"] is False
    assert data_ignored["kcal"] == 135.0
    assert _mock_gemini_default.call_count == 1

    # 3. Reset nutrition failure leaves previous values and does not fail request
    await client.patch(f"/nutrition/dishes/{d['id']}", json={"kcal": 555.0})
    _mock_gemini_default.side_effect = GeminiUnavailable("LLM service unavailable")
    reset_fail_resp = await client.patch(
        f"/nutrition/dishes/{d['id']}",
        json={"reset_nutrition": True},
    )
    assert reset_fail_resp.status_code == 200
    data_failed = reset_fail_resp.json()
    assert data_failed["nutrition_overridden"] is False
    assert data_failed["kcal"] == 555.0


@pytest.mark.asyncio
async def test_update_dish_identical_components_does_not_reestimate(
    client: AsyncClient, _mock_gemini_default: AsyncMock
):
    p1 = (
        await client.post(
            "/nutrition/products", json={"name": "Геркулес", "available": True}
        )
    ).json()

    # Create dish with initial components and note
    d = (
        await client.post(
            "/nutrition/dishes",
            json={
                "name": "Вівсянка ранкова",
                "note": "без цукру",
                "components": [{"product_id": p1["id"], "weight_g": 60}],
            },
        )
    ).json()
    assert d["nutrition_overridden"] is False
    comp_id = d["components"][0]["id"]

    _mock_gemini_default.reset_mock()

    # 1. PATCH with identical components and changed note -> does not call estimator
    patch_note_resp = await client.patch(
        f"/nutrition/dishes/{d['id']}",
        json={
            "note": "з корицею",
            "components": [{"product_id": p1["id"], "weight_g": 60}],
        },
    )
    assert patch_note_resp.status_code == 200
    patched_data = patch_note_resp.json()
    assert patched_data["note"] == "з корицею"
    assert patched_data["components"][0]["id"] == comp_id  # component row not deleted/recreated
    assert _mock_gemini_default.call_count == 0  # estimator not called

    # 2. PATCH with changed weight -> calls estimator
    _mock_gemini_default.reset_mock()
    patch_weight_resp = await client.patch(
        f"/nutrition/dishes/{d['id']}",
        json={
            "components": [{"product_id": p1["id"], "weight_g": 90}],
        },
    )
    assert patch_weight_resp.status_code == 200
    patched_weight_data = patch_weight_resp.json()
    assert patched_weight_data["components"][0]["weight_g"] == 90
    assert _mock_gemini_default.call_count == 1  # estimator called
    call_prompt = _mock_gemini_default.call_args.kwargs["prompt"]
    assert "90 г" in call_prompt


@pytest.mark.asyncio
async def test_create_dish_with_cooked_weight(
    client: AsyncClient, _mock_gemini_default: AsyncMock
):
    _mock_gemini_default.reset_mock()
    p1 = (
        await client.post(
            "/nutrition/products", json={"name": "Вівсянка", "available": True}
        )
    ).json()

    payload = {
        "name": "Вівсяна каша на воді",
        "cooked_weight_g": 250,
        "components": [{"product_id": p1["id"], "weight_g": 60}],
    }
    resp = await client.post("/nutrition/dishes", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Вівсяна каша на воді"
    assert data["cooked_weight_g"] == 250
    assert data["nutrition_overridden"] is False

    assert _mock_gemini_default.call_count == 1
    call_prompt = _mock_gemini_default.call_args.kwargs["prompt"]
    assert "Вівсянка: 60 г" in call_prompt
    assert "вага готової страви: 250 г" in call_prompt
    assert "сумарна харчова цінність інгредієнтів ділиться на вагу готової страви" in call_prompt


@pytest.mark.asyncio
async def test_reestimate_on_cooked_weight_change_for_non_overridden_and_not_overridden(
    client: AsyncClient, _mock_gemini_default: AsyncMock
):
    p1 = (
        await client.post(
            "/nutrition/products", json={"name": "Макарони", "available": True}
        )
    ).json()

    # Part 1: Non-overridden dish triggers re-estimation on cooked_weight_g change
    _mock_gemini_default.reset_mock()
    d1 = (
        await client.post(
            "/nutrition/dishes",
            json={
                "name": "Відварені макарони",
                "cooked_weight_g": 250,
                "components": [{"product_id": p1["id"], "weight_g": 100}],
            },
        )
    ).json()
    assert d1["nutrition_overridden"] is False
    assert d1["cooked_weight_g"] == 250
    assert _mock_gemini_default.call_count == 1

    # 1a. Patch with changed cooked_weight_g -> re-estimates
    _mock_gemini_default.reset_mock()
    patch_cw_resp = await client.patch(
        f"/nutrition/dishes/{d1['id']}",
        json={"cooked_weight_g": 300},
    )
    assert patch_cw_resp.status_code == 200
    data_cw = patch_cw_resp.json()
    assert data_cw["cooked_weight_g"] == 300
    assert _mock_gemini_default.call_count == 1
    prompt = _mock_gemini_default.call_args.kwargs["prompt"]
    assert "вага готової страви: 300 г" in prompt

    # 1b. Patch with identical cooked_weight_g -> does NOT re-estimate
    _mock_gemini_default.reset_mock()
    patch_same_resp = await client.patch(
        f"/nutrition/dishes/{d1['id']}",
        json={"cooked_weight_g": 300, "note": "аль денте"},
    )
    assert patch_same_resp.status_code == 200
    assert patch_same_resp.json()["note"] == "аль денте"
    assert _mock_gemini_default.call_count == 0

    # 1c. Patch with explicit null to clear cooked_weight_g -> re-estimates without cooked weight
    _mock_gemini_default.reset_mock()
    patch_clear_resp = await client.patch(
        f"/nutrition/dishes/{d1['id']}",
        json={"cooked_weight_g": None},
    )
    assert patch_clear_resp.status_code == 200
    data_cleared = patch_clear_resp.json()
    assert data_cleared["cooked_weight_g"] is None
    assert _mock_gemini_default.call_count == 1
    cleared_prompt = _mock_gemini_default.call_args.kwargs["prompt"]
    assert "вага готової страви" not in cleared_prompt
    assert "загальна вага = сума ваг: 100 г" in cleared_prompt

    # 1d. Patch from None to a value -> re-estimates with cooked weight
    _mock_gemini_default.reset_mock()
    patch_set_resp = await client.patch(
        f"/nutrition/dishes/{d1['id']}",
        json={"cooked_weight_g": 280},
    )
    assert patch_set_resp.status_code == 200
    data_set = patch_set_resp.json()
    assert data_set["cooked_weight_g"] == 280
    assert _mock_gemini_default.call_count == 1
    set_prompt = _mock_gemini_default.call_args.kwargs["prompt"]
    assert "вага готової страви: 280 г" in set_prompt

    # Part 2: Dish with nutrition_overridden=True does NOT re-estimate on cooked_weight_g change
    d2 = (
        await client.post(
            "/nutrition/dishes",
            json={
                "name": "Макарони з сиром",
                "kcal": 350.0,
                "protein_g": 12.0,
                "fat_g": 10.0,
                "carbs_g": 50.0,
                "cooked_weight_g": 200,
                "components": [{"product_id": p1["id"], "weight_g": 100}],
            },
        )
    ).json()
    assert d2["nutrition_overridden"] is True
    assert d2["cooked_weight_g"] == 200
    assert d2["kcal"] == 350.0

    _mock_gemini_default.reset_mock()
    patch_overridden_cw = await client.patch(
        f"/nutrition/dishes/{d2['id']}",
        json={"cooked_weight_g": 260},
    )
    assert patch_overridden_cw.status_code == 200
    data_overridden = patch_overridden_cw.json()
    assert data_overridden["cooked_weight_g"] == 260
    assert data_overridden["nutrition_overridden"] is True
    assert data_overridden["kcal"] == 350.0
    assert _mock_gemini_default.call_count == 0  # not called!
