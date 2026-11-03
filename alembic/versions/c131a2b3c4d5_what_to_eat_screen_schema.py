"""what to eat screen schema

Revision ID: c131a2b3c4d5
Revises: b130a2b3c4d5
Create Date: 2026-11-03 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c131a2b3c4d5"
down_revision: str | Sequence[str] | None = "b130a2b3c4d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. menus: add date (Date, NOT NULL, indexed)
    op.add_column("menus", sa.Column("date", sa.Date(), nullable=True))
    op.execute(
        "UPDATE menus SET date = COALESCE(eaten_at::date, created_at::date, CURRENT_DATE)"
    )
    op.alter_column("menus", "date", nullable=False)
    op.create_index("ix_menus_date", "menus", ["date"])

    # 2. menus: make eaten_at nullable for pinned entries
    op.alter_column("menus", "eaten_at", nullable=True)

    # 3. menus: add nullable pinned_dish_id FK to dishes (ON DELETE SET NULL)
    op.add_column("menus", sa.Column("pinned_dish_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_menus_pinned_dish_id_dishes",
        "menus",
        "dishes",
        ["pinned_dish_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_menus_pinned_dish_id", "menus", ["pinned_dish_id"])

    # 4. new table pinned_dish_days
    op.create_table(
        "pinned_dish_days",
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
        sa.Column(
            "dish_id",
            sa.Uuid(),
            sa.ForeignKey("dishes.id", ondelete="CASCADE"),
            index=True,
            nullable=False,
        ),
        sa.Column(
            "date",
            sa.Date(),
            index=True,
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "user_id",
            "dish_id",
            "date",
            name="uq_pinned_dish_days_user_id_dish_id_date",
        ),
    )


def downgrade() -> None:
    op.drop_table("pinned_dish_days")
    op.drop_constraint("fk_menus_pinned_dish_id_dishes", "menus", type_="foreignkey")
    op.drop_index("ix_menus_pinned_dish_id", table_name="menus")
    op.drop_column("menus", "pinned_dish_id")
    op.drop_index("ix_menus_date", table_name="menus")
    op.drop_column("menus", "date")
