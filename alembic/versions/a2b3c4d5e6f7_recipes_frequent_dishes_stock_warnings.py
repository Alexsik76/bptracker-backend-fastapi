"""recipes frequent dishes stock warnings

Revision ID: a2b3c4d5e6f7
Revises: f9a8b7c6d5e4
Create Date: 2026-10-28 11:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a2b3c4d5e6f7"
down_revision: str | Sequence[str] | None = "f9a8b7c6d5e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Delete rows of dependent tables
    op.execute("DELETE FROM menu_items")
    op.execute("DELETE FROM menus")
    op.execute("DELETE FROM menu_days")
    op.execute("DELETE FROM pinned_meal_items")
    op.execute("DELETE FROM pinned_meals")
    op.execute("DELETE FROM dish_components")
    op.execute("DELETE FROM dishes")

    # 2. Create recipes table
    op.create_table(
        "recipes",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.text("uuidv7()"), primary_key=True, nullable=False
        ),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("archived", sa.Boolean(), server_default=sa.text("false"), nullable=False),
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
        sa.UniqueConstraint("user_id", "name", name="uq_recipes_user_id_name"),
    )

    # 3. Create recipe_products table
    op.create_table(
        "recipe_products",
        sa.Column(
            "recipe_id",
            sa.Uuid(),
            sa.ForeignKey("recipes.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "product_id",
            sa.Uuid(),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
    )

    # 4. Create frequent_dishes table
    op.create_table(
        "frequent_dishes",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.text("uuidv7()"), primary_key=True, nullable=False
        ),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "recipe_id",
            sa.Uuid(),
            sa.ForeignKey("recipes.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("cooked_weight_g", sa.Integer(), nullable=False),
        sa.Column("kcal", sa.Float(), nullable=True),
        sa.Column("protein_g", sa.Float(), nullable=True),
        sa.Column("fat_g", sa.Float(), nullable=True),
        sa.Column("carbs_g", sa.Float(), nullable=True),
        sa.Column("fiber_g", sa.Float(), nullable=True),
        sa.Column("sugar_g", sa.Float(), nullable=True),
        sa.Column("sodium_mg", sa.Float(), nullable=True),
        sa.Column(
            "nutrition_overridden",
            sa.Boolean(),
            server_default=sa.text("false"),
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
        sa.CheckConstraint(
            "cooked_weight_g > 0",
            name="chk_frequent_dishes_cooked_weight_g_positive",
        ),
    )

    # 5. Create frequent_dish_components table
    op.create_table(
        "frequent_dish_components",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.text("uuidv7()"), primary_key=True, nullable=False
        ),
        sa.Column(
            "frequent_dish_id",
            sa.Uuid(),
            sa.ForeignKey("frequent_dishes.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "product_id",
            sa.Uuid(),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("weight_g", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "weight_g > 0",
            name="chk_frequent_dish_components_weight_g_positive",
        ),
    )

    # 6. Alter dishes table
    op.add_column(
        "dishes",
        sa.Column(
            "recipe_id",
            sa.Uuid(),
            sa.ForeignKey("recipes.id", ondelete="RESTRICT"),
            nullable=False,
        ),
    )
    op.create_index("ix_dishes_recipe_id", "dishes", ["recipe_id"], unique=False)
    op.add_column("dishes", sa.Column("kcal", sa.Float(), nullable=True))
    op.add_column("dishes", sa.Column("protein_g", sa.Float(), nullable=True))
    op.add_column("dishes", sa.Column("fat_g", sa.Float(), nullable=True))
    op.add_column("dishes", sa.Column("carbs_g", sa.Float(), nullable=True))
    op.add_column("dishes", sa.Column("fiber_g", sa.Float(), nullable=True))
    op.add_column("dishes", sa.Column("sugar_g", sa.Float(), nullable=True))
    op.add_column("dishes", sa.Column("sodium_mg", sa.Float(), nullable=True))

    op.alter_column("dishes", "cooked_weight_g", nullable=False)
    op.create_check_constraint(
        "chk_dishes_cooked_weight_g_positive",
        "dishes",
        "cooked_weight_g > 0",
    )
    op.alter_column("dishes", "remaining_g", nullable=False)

    op.drop_column("dishes", "kind")
    op.drop_column("dishes", "fixed_meal_type")
    dishkind = postgresql.ENUM("ready", "constant", name="dishkind")
    dishkind.drop(op.get_bind(), checkfirst=True)

    # 7. Alter pinned_meal_items table
    op.drop_column("pinned_meal_items", "dish_id")
    op.drop_column("pinned_meal_items", "title")
    op.drop_column("pinned_meal_items", "portion_g")
    op.drop_column("pinned_meal_items", "portion_text")
    op.drop_column("pinned_meal_items", "kcal")
    op.drop_column("pinned_meal_items", "protein_g")
    op.drop_column("pinned_meal_items", "fat_g")
    op.drop_column("pinned_meal_items", "carbs_g")
    op.drop_column("pinned_meal_items", "fiber_g")
    op.drop_column("pinned_meal_items", "sugar_g")
    op.drop_column("pinned_meal_items", "sodium_mg")

    op.add_column(
        "pinned_meal_items",
        sa.Column(
            "frequent_dish_id",
            sa.Uuid(),
            sa.ForeignKey("frequent_dishes.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_pinned_meal_items_frequent_dish_id",
        "pinned_meal_items",
        ["frequent_dish_id"],
        unique=False,
    )


def downgrade() -> None:
    # 1. Revert pinned_meal_items
    op.drop_index("ix_pinned_meal_items_frequent_dish_id", table_name="pinned_meal_items")
    op.drop_column("pinned_meal_items", "frequent_dish_id")

    op.add_column("pinned_meal_items", sa.Column("sodium_mg", sa.Float(), nullable=True))
    op.add_column("pinned_meal_items", sa.Column("sugar_g", sa.Float(), nullable=True))
    op.add_column("pinned_meal_items", sa.Column("fiber_g", sa.Float(), nullable=True))
    op.add_column("pinned_meal_items", sa.Column("carbs_g", sa.Float(), nullable=True))
    op.add_column("pinned_meal_items", sa.Column("fat_g", sa.Float(), nullable=True))
    op.add_column("pinned_meal_items", sa.Column("protein_g", sa.Float(), nullable=True))
    op.add_column("pinned_meal_items", sa.Column("kcal", sa.Float(), nullable=True))
    op.add_column("pinned_meal_items", sa.Column("portion_text", sa.String(), nullable=True))
    op.add_column("pinned_meal_items", sa.Column("portion_g", sa.Integer(), nullable=True))
    op.add_column(
        "pinned_meal_items",
        sa.Column("title", sa.String(), nullable=False, server_default=""),
    )
    op.add_column(
        "pinned_meal_items",
        sa.Column(
            "dish_id",
            sa.Uuid(),
            sa.ForeignKey("dishes.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    # 2. Re-create dishkind enum
    dishkind = postgresql.ENUM("ready", "constant", name="dishkind")
    dishkind.create(op.get_bind(), checkfirst=True)

    # 3. Revert dishes table
    mealtype = postgresql.ENUM(name="mealtype", create_type=False)
    op.add_column("dishes", sa.Column("fixed_meal_type", mealtype, nullable=True))
    op.add_column("dishes", sa.Column("kind", dishkind, nullable=False, server_default="ready"))

    op.alter_column("dishes", "remaining_g", nullable=True)
    op.drop_constraint("chk_dishes_cooked_weight_g_positive", "dishes", type_="check")
    op.alter_column("dishes", "cooked_weight_g", nullable=True)

    op.drop_column("dishes", "sodium_mg")
    op.drop_column("dishes", "sugar_g")
    op.drop_column("dishes", "fiber_g")
    op.drop_column("dishes", "carbs_g")
    op.drop_column("dishes", "fat_g")
    op.drop_column("dishes", "protein_g")
    op.drop_column("dishes", "kcal")

    op.drop_index("ix_dishes_recipe_id", table_name="dishes")
    op.drop_column("dishes", "recipe_id")

    # 4. Drop frequent_dish_components, frequent_dishes, recipe_products, recipes
    op.drop_table("frequent_dish_components")
    op.drop_table("frequent_dishes")
    op.drop_table("recipe_products")
    op.drop_table("recipes")
