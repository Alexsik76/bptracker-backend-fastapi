"""add_time_and_removed_to_menus

Revision ID: 1da6b8d1fe13
Revises: 9160137746f0
Create Date: 2026-10-27 00:48:22.098458

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1da6b8d1fe13"
down_revision: str | Sequence[str] | None = "9160137746f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("menus", sa.Column("time", sa.String(), nullable=True))
    op.add_column(
        "menus",
        sa.Column(
            "removed",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.execute(
        """
        UPDATE menus m
        SET time = sub.meal_time
        FROM (
            SELECT p.user_id,
                   elem->>'name' AS meal_name,
                   elem->>'time' AS meal_time
            FROM nutrition_profiles p,
                 jsonb_array_elements(p.meals) AS elem
        ) sub
        WHERE m.user_id = sub.user_id
          AND m.slot = sub.meal_name
          AND m.time IS NULL;
        """
    )


def downgrade() -> None:
    op.drop_column("menus", "removed")
    op.drop_column("menus", "time")
