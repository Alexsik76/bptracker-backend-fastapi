"""move eaten/skipped status to menu_items

Revision ID: b8e72c0134c4
Revises: 31a87e24b5d6
Create Date: 2026-10-26 18:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8e72c0134c4"
down_revision: str | Sequence[str] | None = "31a87e24b5d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Create the new PG enum menuitemstatus
    menuitemstatus = postgresql.ENUM("eaten", "skipped", name="menuitemstatus")
    menuitemstatus.create(op.get_bind(), checkfirst=True)

    # 2. Add status and eaten_at to menu_items
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

    # 3. Data migration:
    # items of menus with status='eaten' -> 'eaten',
    # eaten_at = coalesce(menus.eaten_at, menus.updated_at)
    # status='skipped' -> 'skipped'
    # others -> NULL
    op.execute(
        """
        UPDATE menu_items
        SET status = 'eaten'::menuitemstatus,
            eaten_at = COALESCE(menus.eaten_at, menus.updated_at)
        FROM menus
        WHERE menu_items.menu_id = menus.id
          AND menus.status = 'eaten'
        """
    )
    op.execute(
        """
        UPDATE menu_items
        SET status = 'skipped'::menuitemstatus
        FROM menus
        WHERE menu_items.menu_id = menus.id
          AND menus.status = 'skipped'
        """
    )

    # 4. Drop menus.eaten_at and menus.status
    op.drop_column("menus", "eaten_at")
    op.drop_column("menus", "status")

    # 5. Drop menustatus enum type
    menustatus = postgresql.ENUM("planned", "incomplete", "eaten", "skipped", name="menustatus")
    menustatus.drop(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    # 1. Recreate menustatus enum type
    menustatus = postgresql.ENUM("planned", "incomplete", "eaten", "skipped", name="menustatus")
    menustatus.create(op.get_bind(), checkfirst=True)

    # 2. Restore menus.status and menus.eaten_at
    op.add_column(
        "menus",
        sa.Column(
            "status",
            postgresql.ENUM(
                "planned", "incomplete", "eaten", "skipped", name="menustatus", create_type=False
            ),
            nullable=False,
            server_default=sa.text("'planned'"),
        ),
    )
    op.add_column(
        "menus",
        sa.Column("eaten_at", sa.DateTime(timezone=True), nullable=True),
    )

    # 3. Derive menus.status and menus.eaten_at from items:
    # all eaten -> eaten, all skipped -> skipped, else planned.
    op.execute(
        """
        WITH item_stats AS (
            SELECT
                menu_id,
                COUNT(*) AS total_items,
                COUNT(CASE WHEN status = 'eaten' THEN 1 END) AS eaten_items,
                COUNT(CASE WHEN status = 'skipped' THEN 1 END) AS skipped_items,
                MAX(eaten_at) AS latest_eaten_at
            FROM menu_items
            GROUP BY menu_id
        )
        UPDATE menus
        SET status = CASE
                WHEN item_stats.total_items > 0
                     AND item_stats.eaten_items = item_stats.total_items
                    THEN 'eaten'::menustatus
                WHEN item_stats.total_items > 0
                     AND item_stats.skipped_items = item_stats.total_items
                    THEN 'skipped'::menustatus
                ELSE 'planned'::menustatus
            END,
            eaten_at = CASE
                WHEN item_stats.total_items > 0
                     AND item_stats.eaten_items = item_stats.total_items
                    THEN item_stats.latest_eaten_at
                ELSE NULL
            END
        FROM item_stats
        WHERE menus.id = item_stats.menu_id
        """
    )
    op.alter_column("menus", "status", server_default=None)

    # 4. Drop columns from menu_items
    op.drop_column("menu_items", "eaten_at")
    op.drop_column("menu_items", "status")

    # 5. Drop menuitemstatus enum
    menuitemstatus = postgresql.ENUM("eaten", "skipped", name="menuitemstatus")
    menuitemstatus.drop(op.get_bind(), checkfirst=True)
