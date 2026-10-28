"""create menu days

Revision ID: f9a8b7c6d5e4
Revises: e8d9c0a1b2f3
Create Date: 2026-10-28 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f9a8b7c6d5e4"
down_revision: str | Sequence[str] | None = "e8d9c0a1b2f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "menu_days",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", "date"),
    )
    op.execute(
        """
        INSERT INTO menu_days (user_id, date, created_at)
        SELECT user_id, date, COALESCE(MIN(created_at), now())
        FROM menus
        GROUP BY user_id, date
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_table("menu_days")
