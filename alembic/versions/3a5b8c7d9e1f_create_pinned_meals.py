"""create_pinned_meals

Revision ID: 3a5b8c7d9e1f
Revises: 1da6b8d1fe13
Create Date: 2026-10-27 02:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3a5b8c7d9e1f"
down_revision: str | Sequence[str] | None = "1da6b8d1fe13"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. pinned_meals
    op.create_table(
        "pinned_meals",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("slot", sa.String(), nullable=False),
        sa.Column(
            "active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "slot", name="uq_pinned_meals_user_id_slot"),
    )
    op.create_index("ix_pinned_meals_user_id", "pinned_meals", ["user_id"])

    # 2. pinned_meal_items
    op.create_table(
        "pinned_meal_items",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("pinned_meal_id", sa.Uuid(), nullable=False),
        sa.Column("dish_id", sa.Uuid(), nullable=True),
        sa.Column("product_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("portion_g", sa.Integer(), nullable=True),
        sa.Column("portion_text", sa.String(), nullable=True),
        sa.Column("kcal_est", sa.Integer(), nullable=True),
        sa.Column(
            "position",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["pinned_meal_id"], ["pinned_meals.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["dish_id"], ["dishes.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "NOT (dish_id IS NOT NULL AND product_id IS NOT NULL)",
            name="chk_pinned_meal_items_single_reference",
        ),
    )
    op.create_index(
        "ix_pinned_meal_items_pinned_meal_id",
        "pinned_meal_items",
        ["pinned_meal_id"],
    )

    # 3. Add pinned_meal_id and pin_dismissed to menus
    op.add_column("menus", sa.Column("pinned_meal_id", sa.Uuid(), nullable=True))
    op.add_column(
        "menus",
        sa.Column(
            "pin_dismissed",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.create_foreign_key(
        "fk_menus_pinned_meal_id_pinned_meals",
        "menus",
        "pinned_meals",
        ["pinned_meal_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_menus_pinned_meal_id_pinned_meals", "menus", type_="foreignkey"
    )
    op.drop_column("menus", "pin_dismissed")
    op.drop_column("menus", "pinned_meal_id")
    op.drop_index("ix_pinned_meal_items_pinned_meal_id", table_name="pinned_meal_items")
    op.drop_table("pinned_meal_items")
    op.drop_index("ix_pinned_meals_user_id", table_name="pinned_meals")
    op.drop_table("pinned_meals")
