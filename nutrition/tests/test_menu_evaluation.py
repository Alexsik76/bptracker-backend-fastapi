from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app_settings import ai_config, cache
from app_settings.registry import REGISTRY, SettingValueError
from config import get_settings
from conftest import test_session_factory as db_session_factory
from nutrition import crud
from nutrition.clock import utc_today
from nutrition.crud.evaluation import save_menu_day_evaluations
from nutrition.models import GenerationLog, MealType, MenuCreate, MenuItemCreate
from nutrition.models.evaluation import (
    MenuDayEvaluation,
    MenuEvaluationCriterion,
)
from nutrition.service.evaluator import evaluate_menu_day, evaluate_menu_day_background
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


async def setup_test_user_profile(client: AsyncClient):
    await client.put(
        "/nutrition/profile",
        json={
            "sex": "male",
            "birth_year": 1990,
            "height_cm": 180,
            "weight_kg": 85.0,
            "target_weight_kg": 75.0,
            "pace_kg_per_week": 0.5,
        },
    )
    dish_resp = await client.post(
        "/nutrition/dishes",
        json={
            "name": "Вівсянка",
            "kcal": 150.0,
            "protein_g": 5.0,
            "fat_g": 3.0,
            "carbs_g": 25.0,
        },
    )
    dish_id = dish_resp.json()["id"]
    return {"dish_id": dish_id}


@pytest.mark.asyncio
async def test_missing_key_skips_silently(session: AsyncSession, test_user_id: UUID, seed_criteria):
    """When typesafe_api_key is None, evaluation is skipped silently without error."""
    test_day = utc_today()
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
async def test_evaluation_stores_rows_and_generation_log(
    client: AsyncClient, session: AsyncSession, test_user_id: UUID, seed_criteria
):
    """Evaluation stores rows and generation log with menu_id."""
    test_day = utc_today()
    data = await setup_test_user_profile(client)
    dish_id = data["dish_id"]

    # Create a meal
    meal = await crud.create_menu(
        session,
        test_user_id,
        MenuCreate(
            meal_type=MealType.BREAKFAST,
            items=[MenuItemCreate(dish_id=dish_id, portion_g=200)],
        ),
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

    configured_settings = get_settings().model_copy(
        update={"typesafe_api_key": "valid-api-key"}
    )
    with (
        patch("nutrition.service.evaluator.async_session_factory", db_session_factory),
        patch(
            "nutrition.service.evaluator.evaluate_systemone", new_callable=AsyncMock
        ) as mock_eval,
    ):
        mock_eval.return_value = mock_jev_result
        await evaluate_menu_day_background(
            user_id=test_user_id,
            menu_date=test_day,
            settings=configured_settings,
            session_factory=db_session_factory,
            menu_id=meal.id,
        )

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
    assert by_key["goal"].menu_id == meal.id
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
    assert log.menu_id == meal.id
    assert log.input_tokens == 150
    assert log.output_tokens == 25
    assert log.raw_response == '{"mock": "response"}'


@pytest.mark.asyncio
async def test_re_evaluation_replaces_rows(
    client: AsyncClient, session: AsyncSession, test_user_id: UUID, seed_criteria
):
    """Re-evaluation replaces existing evaluation rows rather than creating duplicates."""
    test_day = utc_today()
    await setup_test_user_profile(client)
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
    assert len(rows2) == 3
    goal_row_updated = next(r for r in rows2 if r.criterion_key == "goal")
    assert goal_row_updated.choice == "red"
    assert goal_row_updated.confidence == 0.95


@pytest.mark.asyncio
async def test_get_day_evaluations_projects_uncertain_below_threshold(
    session: AsyncSession, test_user_id: UUID, seed_criteria
):
    """get_day_evaluations projects 'uncertain' when confidence*100 < threshold."""
    test_day = utc_today()

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

    evaluations = await crud.get_day_evaluations(session, test_user_id, test_day)
    assert len(evaluations) == 3
    by_key = {e.key: e for e in evaluations}
    assert by_key["goal"].level == "uncertain"
    assert by_key["balance"].level == "yellow"
    assert by_key["salt"].level == "green"
