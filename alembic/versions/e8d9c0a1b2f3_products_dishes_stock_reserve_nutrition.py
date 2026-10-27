"""products dishes stock reserve nutrition

Revision ID: e8d9c0a1b2f3
Revises: c7b8a9d0e1f2
Create Date: 2026-10-27 15:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e8d9c0a1b2f3"
down_revision: str | Sequence[str] | None = "c7b8a9d0e1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Delete rows in dependent tables
    op.execute("DELETE FROM menu_items")
    op.execute("DELETE FROM menus")
    op.execute("DELETE FROM pinned_meal_items")
    op.execute("DELETE FROM dish_components")

    # 2. Products table changes
    op.drop_column("products", "daily_limit_g")
    op.add_column("products", sa.Column("stock_g", sa.Integer(), nullable=True))
    op.add_column("products", sa.Column("kcal", sa.Float(), nullable=True))
    op.add_column("products", sa.Column("protein_g", sa.Float(), nullable=True))
    op.add_column("products", sa.Column("fat_g", sa.Float(), nullable=True))
    op.add_column("products", sa.Column("carbs_g", sa.Float(), nullable=True))
    op.add_column("products", sa.Column("fiber_g", sa.Float(), nullable=True))
    op.add_column("products", sa.Column("sugar_g", sa.Float(), nullable=True))
    op.add_column("products", sa.Column("sodium_mg", sa.Float(), nullable=True))

    # 3. Dish components table changes
    op.drop_column("dish_components", "cooked_weight_g")
    op.drop_column("dish_components", "raw_weight_g")
    op.drop_column("dish_components", "name")
    op.add_column("dish_components", sa.Column("product_id", sa.Uuid(), nullable=False))
    op.add_column("dish_components", sa.Column("weight_g", sa.Integer(), nullable=False))
    op.create_foreign_key(
        "fk_dish_components_product_id_products",
        "dish_components",
        "products",
        ["product_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_dish_components_product_id",
        "dish_components",
        ["product_id"],
        unique=False,
    )
    op.create_check_constraint(
        "chk_dish_components_weight_g_positive",
        "dish_components",
        "weight_g > 0",
    )

    # 4. Create nutritionsource enum
    nutritionsource = postgresql.ENUM("user", "model", "computed", name="nutritionsource")
    nutritionsource.create(op.get_bind(), checkfirst=True)

    # 5. Menu items table changes
    op.drop_constraint("chk_menu_items_single_reference", "menu_items", type_="check")
    op.drop_column("menu_items", "product_id")
    op.drop_column("menu_items", "kcal_est")
    op.drop_column("menu_items", "kcal_source")
    op.add_column("menu_items", sa.Column("kcal", sa.Float(), nullable=True))
    op.add_column("menu_items", sa.Column("protein_g", sa.Float(), nullable=True))
    op.add_column("menu_items", sa.Column("fat_g", sa.Float(), nullable=True))
    op.add_column("menu_items", sa.Column("carbs_g", sa.Float(), nullable=True))
    op.add_column("menu_items", sa.Column("fiber_g", sa.Float(), nullable=True))
    op.add_column("menu_items", sa.Column("sugar_g", sa.Float(), nullable=True))
    op.add_column("menu_items", sa.Column("sodium_mg", sa.Float(), nullable=True))
    op.add_column(
        "menu_items",
        sa.Column(
            "nutrition_source",
            postgresql.ENUM("user", "model", "computed", name="nutritionsource", create_type=False),
            nullable=True,
        ),
    )
    op.add_column(
        "menu_items",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.add_column(
        "menu_items",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    # 6. Pinned meal items table changes
    op.drop_constraint(
        "chk_pinned_meal_items_single_reference", "pinned_meal_items", type_="check"
    )
    op.drop_column("pinned_meal_items", "product_id")
    op.drop_column("pinned_meal_items", "kcal_est")
    op.add_column("pinned_meal_items", sa.Column("kcal", sa.Float(), nullable=True))
    op.add_column("pinned_meal_items", sa.Column("protein_g", sa.Float(), nullable=True))
    op.add_column("pinned_meal_items", sa.Column("fat_g", sa.Float(), nullable=True))
    op.add_column("pinned_meal_items", sa.Column("carbs_g", sa.Float(), nullable=True))
    op.add_column("pinned_meal_items", sa.Column("fiber_g", sa.Float(), nullable=True))
    op.add_column("pinned_meal_items", sa.Column("sugar_g", sa.Float(), nullable=True))
    op.add_column("pinned_meal_items", sa.Column("sodium_mg", sa.Float(), nullable=True))

    # 7. Drop kcalsource enum
    kcalsource = postgresql.ENUM("user", "model", name="kcalsource")
    kcalsource.drop(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    # 1. Restore kcalsource enum
    kcalsource = postgresql.ENUM("user", "model", name="kcalsource")
    kcalsource.create(op.get_bind(), checkfirst=True)

    # 2. Revert pinned_meal_items
    op.drop_column("pinned_meal_items", "sodium_mg")
    op.drop_column("pinned_meal_items", "sugar_g")
    op.drop_column("pinned_meal_items", "fiber_g")
    op.drop_column("pinned_meal_items", "carbs_g")
    op.drop_column("pinned_meal_items", "fat_g")
    op.drop_column("pinned_meal_items", "protein_g")
    op.drop_column("pinned_meal_items", "kcal")
    op.add_column("pinned_meal_items", sa.Column("kcal_est", sa.Integer(), nullable=True))
    op.add_column("pinned_meal_items", sa.Column("product_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "pinned_meal_items_product_id_fkey",
        "pinned_meal_items",
        "products",
        ["product_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "chk_pinned_meal_items_single_reference",
        "pinned_meal_items",
        "NOT (dish_id IS NOT NULL AND product_id IS NOT NULL)",
    )

    # 3. Revert menu_items
    op.drop_column("menu_items", "updated_at")
    op.drop_column("menu_items", "created_at")
    op.drop_column("menu_items", "nutrition_source")
    op.drop_column("menu_items", "sodium_mg")
    op.drop_column("menu_items", "sugar_g")
    op.drop_column("menu_items", "fiber_g")
    op.drop_column("menu_items", "carbs_g")
    op.drop_column("menu_items", "fat_g")
    op.drop_column("menu_items", "protein_g")
    op.drop_column("menu_items", "kcal")
    op.add_column(
        "menu_items",
        sa.Column(
            "kcal_source",
            postgresql.ENUM("user", "model", name="kcalsource", create_type=False),
            nullable=True,
        ),
    )
    op.add_column("menu_items", sa.Column("kcal_est", sa.Integer(), nullable=True))
    op.add_column("menu_items", sa.Column("product_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "menu_items_product_id_fkey",
        "menu_items",
        "products",
        ["product_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "chk_menu_items_single_reference",
        "menu_items",
        "NOT (dish_id IS NOT NULL AND product_id IS NOT NULL)",
    )

    # 4. Drop nutritionsource enum
    nutritionsource = postgresql.ENUM("user", "model", "computed", name="nutritionsource")
    nutritionsource.drop(op.get_bind(), checkfirst=True)

    # 5. Revert dish_components
    op.drop_constraint("chk_dish_components_weight_g_positive", "dish_components", type_="check")
    op.drop_index("ix_dish_components_product_id", table_name="dish_components")
    op.drop_constraint(
        "fk_dish_components_product_id_products", "dish_components", type_="foreignkey"
    )
    op.drop_column("dish_components", "weight_g")
    op.drop_column("dish_components", "product_id")
    op.add_column("dish_components", sa.Column("name", sa.String(), nullable=False))
    op.add_column("dish_components", sa.Column("raw_weight_g", sa.Integer(), nullable=True))
    op.add_column("dish_components", sa.Column("cooked_weight_g", sa.Integer(), nullable=True))

    # 6. Revert products
    op.drop_column("products", "sodium_mg")
    op.drop_column("products", "sugar_g")
    op.drop_column("products", "fiber_g")
    op.drop_column("products", "carbs_g")
    op.drop_column("products", "fat_g")
    op.drop_column("products", "protein_g")
    op.drop_column("products", "kcal")
    op.drop_column("products", "stock_g")
    op.add_column("products", sa.Column("daily_limit_g", sa.Integer(), nullable=True))
