from datetime import UTC, datetime
from datetime import date as dt_date
from uuid import UUID

from sqlmodel import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app_settings import ai_config
from nutrition.models.evaluation import (
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
) -> list[MenuDayEvaluation]:
    """Save or replace evaluations for user and date per criterion_key."""
    stmt = select(MenuDayEvaluation).where(
        MenuDayEvaluation.user_id == user_id,
        MenuDayEvaluation.date == menu_date,
    )
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
    """Fetch evaluations for user and date with read-time projected level, ordered by position."""
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
            )
        )
    return result
