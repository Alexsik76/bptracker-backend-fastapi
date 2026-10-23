"""create_nutrition_module_tables

Revision ID: e4f62ae07b5f
Revises: 7e1ac63ee552
Create Date: 2026-10-23 20:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e4f62ae07b5f"
down_revision: str | Sequence[str] | None = "7e1ac63ee552"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # 1. nutrition_profiles
    op.create_table(
        "nutrition_profiles",
        sa.Column("sex", sa.Enum("male", "female", name="sex"), nullable=False),
        sa.Column("birth_year", sa.Integer(), nullable=False),
        sa.Column("height_cm", sa.Integer(), nullable=False),
        sa.Column("weight_kg", sa.Float(), nullable=False),
        sa.Column("target_weight_kg", sa.Float(), nullable=False),
        sa.Column("pace_kg_per_week", sa.Float(), nullable=False),
        sa.Column("restrictions", sa.String(), nullable=True),
        sa.Column("dislikes", sa.String(), nullable=True),
        sa.Column("meals", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )

    # 2. products
    op.create_table(
        "products",
        sa.Column("name", sa.String(), nullable=False),
        sa.Column(
            "availability",
            sa.Enum("in_stock", "low", "out", name="productavailability"),
            nullable=False,
        ),
        sa.Column(
            "preference",
            sa.Enum("preferred", "neutral", "avoid", name="productpreference"),
            nullable=False,
        ),
        sa.Column("eat_raw", sa.Boolean(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=True),
        sa.Column("daily_limit_g", sa.Integer(), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
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
    )
    op.create_index(op.f("ix_products_user_id"), "products", ["user_id"], unique=False)

    # 3. dishes
    op.create_table(
        "dishes",
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("kind", sa.Enum("ready", "constant", name="dishkind"), nullable=False),
        sa.Column("cooked_weight_g", sa.Integer(), nullable=True),
        sa.Column("remaining_g", sa.Integer(), nullable=True),
        sa.Column("cooked_on", sa.Date(), nullable=True),
        sa.Column("fixed_slot", sa.String(), nullable=True),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
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
        sa.CheckConstraint(
            "(remaining_g IS NULL) OR (remaining_g >= 0)",
            name="chk_dishes_remaining_g_non_negative",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_dishes_user_id"), "dishes", ["user_id"], unique=False)

    # 4. dish_components
    op.create_table(
        "dish_components",
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("raw_weight_g", sa.Integer(), nullable=True),
        sa.Column("cooked_weight_g", sa.Integer(), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("dish_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["dish_id"], ["dishes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_dish_components_dish_id"), "dish_components", ["dish_id"], unique=False
    )

    # 5. menus (slot-level meal with status)
    op.create_table(
        "menus",
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("slot", sa.String(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("planned", "incomplete", "eaten", "skipped", name="menustatus"),
            nullable=False,
            server_default="planned",
        ),
        sa.Column("eaten_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("regenerations", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
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
        sa.UniqueConstraint("user_id", "date", "slot", name="uq_menus_user_id_date_slot"),
    )
    op.create_index(op.f("ix_menus_user_id"), "menus", ["user_id"], unique=False)
    op.create_index(op.f("ix_menus_date"), "menus", ["date"], unique=False)

    # 6. menu_items
    op.create_table(
        "menu_items",
        sa.Column("dish_id", sa.Uuid(), nullable=True),
        sa.Column("product_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("portion_g", sa.Integer(), nullable=True),
        sa.Column("portion_text", sa.String(), nullable=True),
        sa.Column("kcal_est", sa.Integer(), nullable=True),
        sa.Column(
            "origin",
            sa.Enum("generated", "manual", name="menuitemorigin"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("menu_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "NOT (dish_id IS NOT NULL AND product_id IS NOT NULL)",
            name="chk_menu_items_single_reference",
        ),
        sa.ForeignKeyConstraint(["dish_id"], ["dishes.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["menu_id"], ["menus.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_menu_items_menu_id"), "menu_items", ["menu_id"], unique=False)

    # 7. generation_log
    op.create_table(
        "generation_log",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("scope", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("prompt_version", sa.String(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("raw_response", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_generation_log_date"), "generation_log", ["date"], unique=False)
    op.create_index(op.f("ix_generation_log_user_id"), "generation_log", ["user_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_generation_log_user_id"), table_name="generation_log")
    op.drop_index(op.f("ix_generation_log_date"), table_name="generation_log")
    op.drop_table("generation_log")

    op.drop_index(op.f("ix_menu_items_menu_id"), table_name="menu_items")
    op.drop_table("menu_items")
    sa.Enum(name="menuitemorigin").drop(op.get_bind(), checkfirst=True)

    op.drop_index(op.f("ix_menus_date"), table_name="menus")
    op.drop_index(op.f("ix_menus_user_id"), table_name="menus")
    op.drop_table("menus")
    sa.Enum(name="menustatus").drop(op.get_bind(), checkfirst=True)

    op.drop_index(op.f("ix_dish_components_dish_id"), table_name="dish_components")
    op.drop_table("dish_components")

    op.drop_index(op.f("ix_dishes_user_id"), table_name="dishes")
    op.drop_table("dishes")
    sa.Enum(name="dishkind").drop(op.get_bind(), checkfirst=True)

    op.drop_index(op.f("ix_products_user_id"), table_name="products")
    op.drop_table("products")
    sa.Enum(name="productavailability").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="productpreference").drop(op.get_bind(), checkfirst=True)

    op.drop_table("nutrition_profiles")
    sa.Enum(name="sex").drop(op.get_bind(), checkfirst=True)
