from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app_settings import ai_config, cache
from app_settings.registry import REGISTRY, SettingValueError
from config import get_settings
from conftest import test_session_factory as db_session_factory
from gemini_infra import GeminiResult
from main import app
from nutrition.crud.evaluation import save_menu_day_evaluations
from nutrition.models import GenerationLog
from nutrition.models.evaluation import (
    MenuDayEvaluation,
    MenuEvaluationCriterion,
)
from nutrition.service.evaluator import evaluate_menu_day
from typesafe_infra import (
    JevChoiceAnswer,
    JevResult,
    JevUsage,
)

SEED_CRITERIA = [
    {
        "version": 1,
        "position": 1,
        "key": "goal",
        "title": "Мета",
        "instructions": (
            "Оціни лише відповідність загальної калорійності денного меню "
            "денній нормі користувача з урахуванням напрямку його мети "
            "(дефіцит, профіцит, утримання). Склад страв не враховуй."
        ),
        "criteria": {
            "green": "Калорійність відхиляється від денної норми не більше ніж на 10 %",
            "yellow": "Відхилення від 10 до 20 %",
            "red": "Відхилення понад 20 %",
        },
    },
    {
        "version": 1,
        "position": 2,
        "key": "balance",
        "title": "Збалансованість",
        "instructions": (
            "Оціни збалансованість денного меню за трьома умовами: "
            "білковий продукт у кожному основному прийомі (сніданок, обід, вечеря); "
            "овочі або фрукти в меню; "
            "щонайменше чотири групи продуктів із п'яти "
            "(овочі й фрукти, зернові, білкові продукти, молочні продукти, жири). "
            "Калорійність не враховуй."
        ),
        "criteria": {
            "green": "Виконано всі три умови",
            "yellow": "Виконано дві умови",
            "red": "Виконано одну умову або жодної, наприклад меню з самої води",
        },
    },
    {
        "version": 1,
        "position": 3,
        "key": "salt",
        "title": "Сіль",
        "instructions": (
            "Оціни наявність у денному меню солоних і перероблених продуктів: "
            "ковбаси, копченості, консерви, соління, готові соуси, тверді сири, солоні снеки."
        ),
        "criteria": {
            "green": "Таких продуктів немає",
            "yellow": "Один такий продукт",
            "red": "Два і більше таких продуктів",
        },
    },
]


@pytest_asyncio.fixture
async def seed_criteria(session: AsyncSession):
    """Seed version 1 evaluation criteria in test database."""
    for item in SEED_CRITERIA:
        crit = MenuEvaluationCriterion(
            version=item["version"],
            position=item["position"],
            key=item["key"],
            title=item["title"],
            instructions=item["instructions"],
            criteria=item["criteria"],
        )
        session.add(crit)
    await session.commit()


@pytest.fixture(autouse=True)
def clean_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def mock_gemini():
    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as m:
        m.return_value = GeminiResult(
            data={
                "rationale": "Норма",
                "shopping_list": [],
                "items": [
                    {
                        "meal_type": "breakfast",
                        "title": "Вівсянка",
                        "portion_g": 200,
                        "portion_text": "200 г",
                        "position": 0,
                    }
                ],
            },
            raw_text="{}",
            model="gemini-2.0-flash",
            input_tokens=10,
            output_tokens=10,
        )
        yield m


def test_new_settings_registered():
    """Verify ai.jev.* settings in registry, validation rules and accessor functions."""
    assert "ai.jev.model" in REGISTRY
    assert "ai.jev.timeout_seconds" in REGISTRY
    assert "ai.jev.min_confidence_percent" in REGISTRY

    model_def = REGISTRY["ai.jev.model"]
    assert model_def.type == "str"
    assert model_def.env_attr == "jev_model"
    assert model_def.validate("jev-latest") == "jev-latest"
    with pytest.raises(SettingValueError):
        model_def.validate("")

    timeout_def = REGISTRY["ai.jev.timeout_seconds"]
    assert timeout_def.type == "int"
    assert timeout_def.env_attr == "jev_timeout_seconds"
    assert timeout_def.min == 2
    assert timeout_def.max == 60
    assert timeout_def.validate(10) == 10
    with pytest.raises(SettingValueError):
        timeout_def.validate(1)
    with pytest.raises(SettingValueError):
        timeout_def.validate(61)

    min_conf_def = REGISTRY["ai.jev.min_confidence_percent"]
    assert min_conf_def.type == "int"
    assert min_conf_def.env_attr == "jev_min_confidence_percent"
    assert min_conf_def.min == 0
    assert min_conf_def.max == 100
    assert min_conf_def.validate(50) == 50
    with pytest.raises(SettingValueError):
        min_conf_def.validate(-1)
    with pytest.raises(SettingValueError):
        min_conf_def.validate(101)

    # Test accessor functions
    assert ai_config.jev_model() == "jev-latest"
    assert ai_config.jev_timeout_seconds() == 10
    assert ai_config.jev_min_confidence_percent() == 50


async def setup_test_user_profile(client: AsyncClient, test_day):
    await client.put(
        "/nutrition/profile",
        json={
            "sex": "male",
            "birth_year": 1990,
            "height_cm": 180,
            "weight_kg": 85.0,
            "target_weight_kg": 75.0,
            "pace_kg_per_week": 0.5,
            "meals": [
                {"type": "breakfast", "time": "08:00"},
                {"type": "lunch", "time": "13:00"},
                {"type": "dinner", "time": "19:00"},
            ],
        },
    )
    dish_resp = await client.post(
        "/nutrition/dishes",
        json={
            "name": "Вівсянка",
            "cooked_weight_g": 300,
            "kcal": 150.0,
            "protein_g": 5.0,
            "fat_g": 3.0,
            "carbs_g": 25.0,
        },
    )
    dish_id = dish_resp.json()["id"]
    await client.post(f"/nutrition/menus/{test_day}/create")
    return {"dish_id": dish_id}


@pytest.mark.asyncio
async def test_missing_key_skips_silently(session: AsyncSession, test_user_id, seed_criteria):
    """When typesafe_api_key is None, evaluation is skipped silently without error."""
    test_day = datetime.now(UTC).date() + timedelta(days=2)
    settings = get_settings().model_copy(update={"typesafe_api_key": None})

    with patch("typesafe_infra.client.httpx.AsyncClient") as mock_client:
        await evaluate_menu_day(
            session=session,
            user_id=test_user_id,
            menu_date=test_day,
            settings=settings,
        )
        mock_client.assert_not_called()

    # Verify no evaluation rows were stored
    evals = (
        await session.exec(
            select(MenuDayEvaluation).where(MenuDayEvaluation.user_id == test_user_id)
        )
    ).all()
    assert len(evals) == 0


@pytest.mark.asyncio
async def test_generation_triggers_evaluation_and_rows_stored(
    client: AsyncClient, session: AsyncSession, test_user_id, seed_criteria
):
    """Successful generation triggers background evaluation, stores rows and generation log."""
    test_day = datetime.now(UTC).date() + timedelta(days=2)
    data = await setup_test_user_profile(client, test_day)
    dish_id = data["dish_id"]

    mock_gemini_response = GeminiResult(
        data={
            "rationale": "Норма",
            "shopping_list": [],
            "items": [
                {
                    "meal_type": "breakfast",
                    "dish_id": dish_id,
                    "title": "Вівсянка",
                    "portion_g": 200,
                    "portion_text": "200 г",
                    "position": 1,
                }
            ],
        },
        raw_text="{}",
        model="gemini-2.0-flash",
        input_tokens=10,
        output_tokens=10,
    )

    mock_jev_result = JevResult(
        model="jev-latest",
        answers={
            "goal": JevChoiceAnswer(
                choice="green",
                confidence=0.8,
                probabilities={"green": 0.8, "yellow": 0.15, "red": 0.05},
            ),
            "balance": JevChoiceAnswer(
                choice="yellow",
                confidence=0.7,
                probabilities={"green": 0.2, "yellow": 0.7, "red": 0.1},
            ),
            "salt": JevChoiceAnswer(
                choice="green",
                confidence=0.9,
                probabilities={"green": 0.9, "yellow": 0.1, "red": 0.0},
            ),
        },
        usage=JevUsage(input_tokens=150, output_tokens=25),
        raw_response='{"mock": "response"}',
    )

    with (
        patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_gen,
        patch("nutrition.service.evaluator.async_session_factory", db_session_factory),
        patch(
            "nutrition.service.evaluator.evaluate_systemone", new_callable=AsyncMock
        ) as mock_eval,
    ):
        mock_gen.return_value = mock_gemini_response
        configured_settings = get_settings().model_copy(
            update={"typesafe_api_key": "valid-api-key"}
        )
        app.dependency_overrides[get_settings] = lambda: configured_settings
        mock_eval.return_value = mock_jev_result

        try:
            # Trigger generation endpoint
            resp = await client.post(
                f"/nutrition/menus/{test_day}/generate",
                json={"scope": "day"},
            )
            assert resp.status_code == 200
        finally:
            app.dependency_overrides.pop(get_settings, None)

        # Background task runs: verify JEV was called
        mock_eval.assert_awaited_once()
        _, kwargs = mock_eval.await_args
        state = kwargs["state"]
        questions = kwargs["questions"]

        # Check state: user goal has direction & norm; day menu has meals & day_total_kcal
        assert "user_goal" in state
        assert state["user_goal"]["goal_direction"] == "дефіцит"
        assert state["user_goal"]["daily_kcal_norm"] > 0
        assert "day_menu" in state
        assert "meals" in state["day_menu"]
        assert "day_total_kcal" in state["day_menu"]

        # Check questions: all criteria in one request
        assert set(questions.keys()) == {"goal", "balance", "salt"}
        for q in questions.values():
            assert q["type"] == "choice"
            assert "instructions" in q
            assert set(q["criteria"].keys()) == {"green", "yellow", "red"}

    # Check rows stored in DB
    eval_rows = (
        await session.exec(
            select(MenuDayEvaluation)
            .where(
                MenuDayEvaluation.user_id == test_user_id,
                MenuDayEvaluation.date == test_day,
            )
            .order_by(MenuDayEvaluation.criterion_key)
        )
    ).all()
    assert len(eval_rows) == 3
    by_key = {r.criterion_key: r for r in eval_rows}
    assert by_key["goal"].choice == "green"
    assert by_key["goal"].confidence == 0.8
    assert by_key["balance"].choice == "yellow"
    assert by_key["salt"].choice == "green"

    # Check generation_log
    log_rows = (
        await session.exec(
            select(GenerationLog).where(
                GenerationLog.user_id == test_user_id,
                GenerationLog.date == test_day,
                GenerationLog.scope == "menu_evaluation",
            )
        )
    ).all()
    assert len(log_rows) == 1
    log = log_rows[0]
    assert log.model == "jev-latest"
    assert log.input_tokens == 150
    assert log.output_tokens == 25
    assert log.raw_response == '{"mock": "response"}'


@pytest.mark.asyncio
async def test_re_evaluation_replaces_rows(
    client: AsyncClient, session: AsyncSession, test_user_id, seed_criteria
):
    """Re-evaluation replaces existing evaluation rows rather than creating duplicates."""
    test_day = datetime.now(UTC).date() + timedelta(days=2)
    await setup_test_user_profile(client, test_day)
    settings = get_settings().model_copy(update={"typesafe_api_key": "valid-api-key"})

    # 1. First evaluation: goal is green
    first_res = JevResult(
        model="jev-latest",
        answers={
            "goal": JevChoiceAnswer(choice="green", confidence=0.8, probabilities={}),
            "balance": JevChoiceAnswer(choice="green", confidence=0.7, probabilities={}),
            "salt": JevChoiceAnswer(choice="green", confidence=0.9, probabilities={}),
        },
        usage=JevUsage(100, 20),
        raw_response="{}",
    )
    with patch(
        "nutrition.service.evaluator.evaluate_systemone", new_callable=AsyncMock
    ) as mock_eval:
        mock_eval.return_value = first_res
        await evaluate_menu_day(session, test_user_id, test_day, settings)
        await session.commit()

    rows1 = (
        await session.exec(
            select(MenuDayEvaluation).where(
                MenuDayEvaluation.user_id == test_user_id,
                MenuDayEvaluation.date == test_day,
            )
        )
    ).all()
    assert len(rows1) == 3
    goal_row = next(r for r in rows1 if r.criterion_key == "goal")
    assert goal_row.choice == "green"

    # 2. Second evaluation: goal is now red
    second_res = JevResult(
        model="jev-latest",
        answers={
            "goal": JevChoiceAnswer(choice="red", confidence=0.95, probabilities={}),
            "balance": JevChoiceAnswer(choice="yellow", confidence=0.6, probabilities={}),
            "salt": JevChoiceAnswer(choice="yellow", confidence=0.6, probabilities={}),
        },
        usage=JevUsage(100, 20),
        raw_response="{}",
    )
    with patch(
        "nutrition.service.evaluator.evaluate_systemone", new_callable=AsyncMock
    ) as mock_eval:
        mock_eval.return_value = second_res
        await evaluate_menu_day(session, test_user_id, test_day, settings)
        await session.commit()

    rows2 = (
        await session.exec(
            select(MenuDayEvaluation).where(
                MenuDayEvaluation.user_id == test_user_id,
                MenuDayEvaluation.date == test_day,
            )
        )
    ).all()
    # Total count must remain 3 (no duplicate rows)
    assert len(rows2) == 3
    goal_row_updated = next(r for r in rows2 if r.criterion_key == "goal")
    assert goal_row_updated.choice == "red"
    assert goal_row_updated.confidence == 0.95


@pytest.mark.asyncio
async def test_get_returns_evaluations_with_uncertain_below_threshold(
    client: AsyncClient, session: AsyncSession, test_user_id, seed_criteria
):
    """GET /nutrition/menus/{date} projects 'uncertain' when confidence*100 < threshold."""
    test_day = datetime.now(UTC).date() + timedelta(days=2)
    await setup_test_user_profile(client, test_day)

    # Save evaluations with different confidence levels:
    # goal: confidence 0.40 (40%) -> below 50% threshold -> uncertain
    # balance: confidence 0.80 (80%) -> above 50% threshold -> stored choice yellow
    # salt: confidence 0.50 (50%) -> equal to 50% threshold -> stored choice green
    answers = {
        "goal": JevChoiceAnswer(choice="green", confidence=0.40, probabilities={"green": 0.4}),
        "balance": JevChoiceAnswer(choice="yellow", confidence=0.80, probabilities={"yellow": 0.8}),
        "salt": JevChoiceAnswer(choice="green", confidence=0.50, probabilities={"green": 0.5}),
    }
    await save_menu_day_evaluations(
        session=session,
        user_id=test_user_id,
        menu_date=test_day,
        criteria_version=1,
        answers=answers,
        model="jev-latest",
    )
    await session.commit()

    # 1. Default threshold is 50%
    resp = await client.get(f"/nutrition/menus/{test_day}")
    assert resp.status_code == 200
    data = resp.json()
    evals = data.get("evaluations", [])
    assert len(evals) == 3

    # Ordered by criterion position: goal (pos 1), balance (pos 2), salt (pos 3)
    assert evals[0]["key"] == "goal"
    assert evals[0]["title"] == "Мета"
    assert evals[0]["level"] == "uncertain"
    assert evals[0]["confidence"] == 0.40
    assert evals[0]["criteria_version"] == 1
    assert "evaluated_at" in evals[0]

    assert evals[1]["key"] == "balance"
    assert evals[1]["title"] == "Збалансованість"
    assert evals[1]["level"] == "yellow"
    assert evals[1]["confidence"] == 0.80

    assert evals[2]["key"] == "salt"
    assert evals[2]["title"] == "Сіль"
    assert evals[2]["level"] == "green"
    assert evals[2]["confidence"] == 0.50

    # 2. Change cached threshold to 30% -> goal confidence (40%) is now >= threshold
    cache.set_cached("ai.jev.min_confidence_percent", 30)
    resp2 = await client.get(f"/nutrition/menus/{test_day}")
    assert resp2.status_code == 200
    evals2 = resp2.json().get("evaluations", [])
    assert evals2[0]["key"] == "goal"
    assert evals2[0]["level"] == "green"

    # 3. Date without evaluations returns empty list
    other_day = test_day + timedelta(days=5)
    await client.post(f"/nutrition/menus/{other_day}/create")
    resp_empty = await client.get(f"/nutrition/menus/{other_day}")
    assert resp_empty.status_code == 200
    assert resp_empty.json().get("evaluations") == []


@pytest.mark.asyncio
async def test_evaluation_failure_does_not_fail_generation(
    client: AsyncClient, session: AsyncSession, test_user_id, seed_criteria
):
    """JEV service failure does not fail generation request and records log."""
    from typesafe_infra import JevUnavailable

    test_day = datetime.now(UTC).date() + timedelta(days=2)
    data = await setup_test_user_profile(client, test_day)
    dish_id = data["dish_id"]

    mock_gemini_response = GeminiResult(
        data={
            "rationale": "Норма",
            "shopping_list": [],
            "items": [
                {
                    "meal_type": "breakfast",
                    "dish_id": dish_id,
                    "title": "Вівсянка",
                    "portion_g": 200,
                    "portion_text": "200 г",
                    "position": 1,
                }
            ],
        },
        raw_text="{}",
        model="gemini-2.0-flash",
        input_tokens=10,
        output_tokens=10,
    )

    with (
        patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_gen,
        patch("nutrition.service.evaluator.async_session_factory", db_session_factory),
        patch(
            "nutrition.service.evaluator.evaluate_systemone", new_callable=AsyncMock
        ) as mock_eval,
    ):
        mock_gen.return_value = mock_gemini_response
        configured_settings = get_settings().model_copy(
            update={"typesafe_api_key": "valid-api-key"}
        )
        app.dependency_overrides[get_settings] = lambda: configured_settings
        mock_eval.side_effect = JevUnavailable("JEV timeout")

        try:
            resp = await client.post(
                f"/nutrition/menus/{test_day}/generate",
                json={"scope": "day"},
            )
            assert resp.status_code == 200
        finally:
            app.dependency_overrides.pop(get_settings, None)

    # Error row recorded in generation_log
    log_rows = (
        await session.exec(
            select(GenerationLog).where(
                GenerationLog.user_id == test_user_id,
                GenerationLog.date == test_day,
                GenerationLog.scope == "menu_evaluation",
            )
        )
    ).all()
    assert len(log_rows) == 1
    assert log_rows[0].raw_response is None


@pytest.mark.asyncio
async def test_create_and_estimate_endpoints_return_evaluations(
    client: AsyncClient, session: AsyncSession, test_user_id, seed_criteria
):
    """create_day and estimate endpoints include evaluations in DayMenuRead."""
    test_day = datetime.now(UTC).date() + timedelta(days=3)
    await setup_test_user_profile(client, test_day)

    # 1. create_day returns empty evaluations
    create_resp = await client.post(f"/nutrition/menus/{test_day}/create")
    # If already created in setup_test_user_profile, test_day was created
    day2 = test_day + timedelta(days=1)
    create_resp = await client.post(f"/nutrition/menus/{day2}/create")
    assert create_resp.status_code == 201
    assert create_resp.json().get("evaluations") == []

    # 2. Add an evaluation row for test_day and call estimate
    answers = {
        "goal": JevChoiceAnswer(choice="green", confidence=0.8, probabilities={}),
    }
    await save_menu_day_evaluations(
        session=session,
        user_id=test_user_id,
        menu_date=test_day,
        criteria_version=1,
        answers=answers,
        model="jev-latest",
    )
    await session.commit()

    # Calling estimate on a day with no items to estimate returns day with evaluations
    est_resp = await client.post(f"/nutrition/menus/{test_day}/estimate")
    assert est_resp.status_code == 200
    est_data = est_resp.json()
    assert "evaluations" in est_data
    assert len(est_data["evaluations"]) == 1
    assert est_data["evaluations"][0]["key"] == "goal"

