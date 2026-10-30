"""create menu evaluation tables

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
Create Date: 2026-10-30 11:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d3e4f5a6b7c8"
down_revision: str | Sequence[str] | None = "c2d3e4f5a6b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "menu_evaluation_criteria",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("uuidv7()"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=False),
        sa.Column(
            "criteria",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "position",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "version", "key", name="uq_menu_evaluation_criteria_version_key"
        ),
    )

    op.create_table(
        "menu_day_evaluations",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("uuidv7()"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            index=True,
            nullable=False,
        ),
        sa.Column("date", sa.Date(), index=True, nullable=False),
        sa.Column("criterion_key", sa.String(), nullable=False),
        sa.Column("criteria_version", sa.Integer(), nullable=False),
        sa.Column("choice", sa.String(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column(
            "probabilities",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "user_id",
            "date",
            "criterion_key",
            name="uq_menu_day_evaluations_user_date_criterion_key",
        ),
    )

    criteria_table = sa.table(
        "menu_evaluation_criteria",
        sa.column("version", sa.Integer),
        sa.column("key", sa.String),
        sa.column("title", sa.String),
        sa.column("instructions", sa.Text),
        sa.column("criteria", postgresql.JSONB(astext_type=sa.Text())),
        sa.column("position", sa.Integer),
    )

    op.bulk_insert(
        criteria_table,
        [
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
                    "red": (
                        "Виконано одну умову або жодної, "
                        "наприклад меню з самої води"
                    ),
                },
            },
            {
                "version": 1,
                "position": 3,
                "key": "salt",
                "title": "Сіль",
                "instructions": (
                    "Оціни наявність у денному меню солоних і перероблених продуктів: "
                    "ковбаси, копченості, консерви, соління, готові соуси, "
                    "тверді сири, солоні снеки."
                ),
                "criteria": {
                    "green": "Таких продуктів немає",
                    "yellow": "Один такий продукт",
                    "red": "Два і більше таких продуктів",
                },
            },
        ],
    )


def downgrade() -> None:
    op.drop_table("menu_day_evaluations")
    op.drop_table("menu_evaluation_criteria")
