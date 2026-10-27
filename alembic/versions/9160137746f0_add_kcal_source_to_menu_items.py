"""add_kcal_source_to_menu_items

Revision ID: 9160137746f0
Revises: b8e72c0134c4
Create Date: 2026-10-27 00:18:49.462574

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9160137746f0"
down_revision: str | Sequence[str] | None = "b8e72c0134c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Create the new PG enum kcalsource
    kcalsource = postgresql.ENUM("user", "model", name="kcalsource")
    kcalsource.create(op.get_bind(), checkfirst=True)

    # 2. Add kcal_source to menu_items
    op.add_column(
        "menu_items",
        sa.Column(
            "kcal_source",
            postgresql.ENUM("user", "model", name="kcalsource", create_type=False),
            nullable=True,
        ),
    )

    # 3. Backfill existing rows with kcal_est:
    # manual origin -> 'user', generated origin -> 'model'
    op.execute(
        """
        UPDATE menu_items
        SET kcal_source = CASE
            WHEN origin = 'manual' THEN 'user'::kcalsource
            WHEN origin = 'generated' THEN 'model'::kcalsource
            ELSE NULL
        END
        WHERE kcal_est IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_column("menu_items", "kcal_source")
    kcalsource = postgresql.ENUM("user", "model", name="kcalsource")
    kcalsource.drop(op.get_bind(), checkfirst=True)
