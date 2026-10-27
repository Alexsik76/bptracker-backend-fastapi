"""move eaten mark to menus

Revision ID: b9e8d7c6b5a4
Revises: 3a5b8c7d9e1f
Create Date: 2026-10-27 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b9e8d7c6b5a4"
down_revision: str | Sequence[str] | None = "3a5b8c7d9e1f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. DELETE all rows from menu_items and menus (test data, not migrated)
    op.execute("DELETE FROM menu_items")
    op.execute("DELETE FROM menus")

    # 2. Add menus.eaten_at TIMESTAMPTZ NULL
    op.add_column(
        "menus",
        sa.Column("eaten_at", sa.DateTime(timezone=True), nullable=True),
    )

    # 3. Drop menu_items.status and menu_items.eaten_at
    op.drop_column("menu_items", "eaten_at")
    op.drop_column("menu_items", "status")

    # 4. Drop menuitemstatus enum type
    menuitemstatus = postgresql.ENUM("eaten", "skipped", name="menuitemstatus")
    menuitemstatus.drop(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    # 1. Recreate menuitemstatus enum type
    menuitemstatus = postgresql.ENUM("eaten", "skipped", name="menuitemstatus")
    menuitemstatus.create(op.get_bind(), checkfirst=True)

    # 2. Restore menu_items.status and menu_items.eaten_at
    op.add_column(
        "menu_items",
        sa.Column(
            "status",
            postgresql.ENUM("eaten", "skipped", name="menuitemstatus", create_type=False),
            nullable=True,
        ),
    )
    op.add_column(
        "menu_items",
        sa.Column("eaten_at", sa.DateTime(timezone=True), nullable=True),
    )

    # 3. Drop menus.eaten_at
    op.drop_column("menus", "eaten_at")
