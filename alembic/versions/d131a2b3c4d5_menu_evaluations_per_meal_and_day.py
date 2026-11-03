"""menu evaluations per meal and day

Revision ID: d131a2b3c4d5
Revises: c131a2b3c4d5
Create Date: 2026-11-03 12:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d131a2b3c4d5"
down_revision: str | Sequence[str] | None = "c131a2b3c4d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_menu_day_evaluations_user_date_criterion_key",
        "menu_day_evaluations",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_menu_day_evaluations_user_date_criterion_key_menu_id",
        "menu_day_evaluations",
        ["user_id", "date", "criterion_key", "menu_id"],
        postgresql_nulls_not_distinct=True,
    )


def downgrade() -> None:
    # Per-meal labels cannot coexist with the old one-row-per-day constraint.
    op.execute("DELETE FROM menu_day_evaluations WHERE menu_id IS NOT NULL")
    op.drop_constraint(
        "uq_menu_day_evaluations_user_date_criterion_key_menu_id",
        "menu_day_evaluations",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_menu_day_evaluations_user_date_criterion_key",
        "menu_day_evaluations",
        ["user_id", "date", "criterion_key"],
    )
