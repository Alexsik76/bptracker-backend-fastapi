from datetime import UTC, datetime
from datetime import date as dt_date
from uuid import UUID

from sqlmodel import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app_settings import ai_config
from nutrition.models.evaluation import (
    MealEvaluationRead,
    MenuDayEvaluation,
    MenuEvaluationCriterion,
    MenuEvaluationRead,
)
from typesafe_infra.types import JevChoiceAnswer


async def get_active_evaluation_criteria(
    session: AsyncSession,
) -> list[MenuEvaluationCriterion]:
    """Fetch criteria from the highest version, ordered by position."""
    stmt_max = select(func.max(MenuEvaluationCriterion.version))
    max_ver = (await session.exec(stmt_max)).first()
    if max_ver is None:
        return []
    stmt = (
        select(MenuEvaluationCriterion)
        .where(MenuEvaluationCriterion.version == max_ver)
        .order_by(MenuEvaluationCriterion.position)
    )
    return list((await session.exec(stmt)).all())


async def save_menu_day_evaluations(
    session: AsyncSession,
    user_id: UUID,
    menu_date: dt_date,
    criteria_version: int,
    answers: dict[str, JevChoiceAnswer],
    model: str,
    menu_id: UUID | None = None,
) -> list[MenuDayEvaluation]:
    """Save or replace evaluations for user, date and menu_id per criterion_key."""
    where_clause = [
        MenuDayEvaluation.user_id == user_id,
        MenuDayEvaluation.date == menu_date,
    ]
    if menu_id is None:
        where_clause.append(MenuDayEvaluation.menu_id.is_(None))
    else:
        where_clause.append(MenuDayEvaluation.menu_id == menu_id)

    stmt = select(MenuDayEvaluation).where(*where_clause)
    existing_rows = list((await session.exec(stmt)).all())
    existing_by_key = {row.criterion_key: row for row in existing_rows}

    saved = []
    now = datetime.now(UTC)
    for key, ans in answers.items():
        if key in existing_by_key:
            row = existing_by_key[key]
            row.criteria_version = criteria_version
            row.choice = ans.choice
            row.confidence = ans.confidence
            row.probabilities = ans.probabilities
            row.model = model
            row.menu_id = menu_id
            row.created_at = now
            session.add(row)
            saved.append(row)
        else:
            new_row = MenuDayEvaluation(
                user_id=user_id,
                date=menu_date,
                criterion_key=key,
                criteria_version=criteria_version,
                choice=ans.choice,
                confidence=ans.confidence,
                probabilities=ans.probabilities,
                model=model,
                menu_id=menu_id,
                created_at=now,
            )
            session.add(new_row)
            saved.append(new_row)

    await session.flush()
    return saved


async def get_day_evaluations(
    session: AsyncSession,
    user_id: UUID,
    menu_date: dt_date,
) -> list[MenuEvaluationRead]:
    """Fetch day summary evaluations for user and date with read-time projected level.

    Ordered by position.
    """
    stmt = (
        select(MenuDayEvaluation, MenuEvaluationCriterion)
        .outerjoin(
            MenuEvaluationCriterion,
            (MenuDayEvaluation.criterion_key == MenuEvaluationCriterion.key)
            & (MenuDayEvaluation.criteria_version == MenuEvaluationCriterion.version),
        )
        .where(
            MenuDayEvaluation.user_id == user_id,
            MenuDayEvaluation.date == menu_date,
            MenuDayEvaluation.menu_id.is_(None),
        )
        .order_by(MenuEvaluationCriterion.position, MenuDayEvaluation.criterion_key)
    )
    rows = (await session.exec(stmt)).all()
    if not rows:
        return []

    min_confidence = ai_config.jev_min_confidence_percent()
    result = []
    for eval_row, criterion in rows:
        title = criterion.title if criterion else eval_row.criterion_key
        if (eval_row.confidence * 100.0) < min_confidence:
            level = "uncertain"
        else:
            level = eval_row.choice

        result.append(
            MenuEvaluationRead(
                key=eval_row.criterion_key,
                title=title,
                level=level,
                confidence=eval_row.confidence,
                criteria_version=eval_row.criteria_version,
                evaluated_at=eval_row.created_at or datetime.now(UTC),
                menu_id=eval_row.menu_id,
            )
        )
    return result


async def get_meal_evaluations_for_menus(
    session: AsyncSession,
    user_id: UUID,
    menu_ids: list[UUID],
) -> dict[UUID, list[MealEvaluationRead]]:
    """Fetch per-meal evaluations for given menus, ordered by position."""
    if not menu_ids:
        return {}

    stmt = (
        select(MenuDayEvaluation, MenuEvaluationCriterion)
        .outerjoin(
            MenuEvaluationCriterion,
            (MenuDayEvaluation.criterion_key == MenuEvaluationCriterion.key)
            & (MenuDayEvaluation.criteria_version == MenuEvaluationCriterion.version),
        )
        .where(
            MenuDayEvaluation.user_id == user_id,
            MenuDayEvaluation.menu_id.in_(menu_ids),
        )
        .order_by(MenuEvaluationCriterion.position, MenuDayEvaluation.criterion_key)
    )
    rows = (await session.exec(stmt)).all()
    min_confidence = ai_config.jev_min_confidence_percent()
    result: dict[UUID, list[MealEvaluationRead]] = {mid: [] for mid in menu_ids}
    for eval_row, _criterion in rows:
        if eval_row.menu_id is None:
            continue
        if (eval_row.confidence * 100.0) < min_confidence:
            level = "uncertain"
        else:
            level = eval_row.choice

        result[eval_row.menu_id].append(
            MealEvaluationRead(
                criterion_key=eval_row.criterion_key,
                choice=eval_row.choice,
                level=level,
            )
        )
    return result
