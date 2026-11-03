"""simplify nutrition concept

Revision ID: b130a2b3c4d5
Revises: f1e2d3c4b5a6
Create Date: 2026-11-02 23:45:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b130a2b3c4d5"
down_revision: str | Sequence[str] | None = "f1e2d3c4b5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Clean existing dish, meal, and obsolete table data
    op.execute("DELETE FROM menu_day_evaluations")
    op.execute("DELETE FROM generation_log")
    op.execute("DELETE FROM menu_items")
    op.execute("DELETE FROM menus")
    op.execute("DELETE FROM dish_components")
    op.execute("DELETE FROM dishes")
    op.execute("DELETE FROM frequent_dish_components")
    op.execute("DELETE FROM frequent_dishes")
    op.execute("DELETE FROM recipe_products")
    op.execute("DELETE FROM recipes")
    op.execute("DELETE FROM pinned_meal_items")
    op.execute("DELETE FROM pinned_meals")
    op.execute("DELETE FROM menu_notes")
    op.execute("DELETE FROM off_plan_entries")
    op.execute("DELETE FROM menu_days")

    # 2. Drop constraints & foreign keys from tables referencing dropped tables
    op.drop_constraint("fk_menus_pinned_meal_id_pinned_meals", "menus", type_="foreignkey")
    op.drop_index("ix_dishes_recipe_id", table_name="dishes")
    op.drop_column("dishes", "recipe_id")

    # 3. Drop obsolete tables
    op.drop_table("pinned_meal_items")
    op.drop_table("pinned_meals")
    op.drop_table("frequent_dish_components")
    op.drop_table("frequent_dishes")
    op.drop_table("recipe_products")
    op.drop_table("recipes")
    op.drop_index("ix_menu_notes_user_id", table_name="menu_notes")
    op.drop_index("ix_menu_notes_date", table_name="menu_notes")
    op.drop_table("menu_notes")
    op.drop_index("ix_off_plan_entries_user_id", table_name="off_plan_entries")
    op.drop_index("ix_off_plan_entries_date", table_name="off_plan_entries")
    op.drop_table("off_plan_entries")
    op.drop_table("menu_days")

    # 4. Reshape products
    op.add_column("products", sa.Column("available", sa.Boolean(), nullable=True))
    op.execute("UPDATE products SET available = (availability IN ('in_stock', 'low'))")
    op.alter_column("products", "available", nullable=False, server_default=sa.text("true"))
    op.add_column("products", sa.Column("note", sa.String(), nullable=True))
    op.drop_column("products", "availability")
    op.drop_column("products", "preference")
    op.drop_column("products", "eat_raw")
    op.drop_column("products", "count")
    op.drop_column("products", "stock_g")
    op.drop_column("products", "kcal")
    op.drop_column("products", "protein_g")
    op.drop_column("products", "fat_g")
    op.drop_column("products", "carbs_g")
    op.drop_column("products", "fiber_g")
    op.drop_column("products", "sugar_g")
    op.drop_column("products", "sodium_mg")

    # 5. Reshape dishes
    op.drop_constraint("chk_dishes_cooked_weight_g_positive", "dishes", type_="check")
    op.drop_constraint("chk_dishes_remaining_g_non_negative", "dishes", type_="check")
    op.drop_column("dishes", "cooked_weight_g")
    op.drop_column("dishes", "remaining_g")
    op.drop_column("dishes", "cooked_on")
    op.drop_column("dishes", "notes")

    mealtype_enum = postgresql.ENUM(
        "breakfast",
        "lunch",
        "dinner",
        "morning_snack",
        "afternoon_snack",
        "evening_snack",
        name="mealtype",
        create_type=False,
    )
    op.add_column(
        "dishes",
        sa.Column(
            "is_ready",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        "dishes",
        sa.Column("pinned_meal_type", mealtype_enum, nullable=True),
    )
    op.add_column(
        "dishes",
        sa.Column(
            "nutrition_overridden",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column("dishes", sa.Column("note", sa.String(), nullable=True))

    # 6. Reshape menus
    op.drop_constraint("uq_menus_user_id_date_meal_type", "menus", type_="unique")
    op.drop_index("ix_menus_date", table_name="menus")
    op.drop_column("menus", "date")
    op.drop_column("menus", "time")
    op.drop_column("menus", "regenerations")
    op.drop_column("menus", "removed")
    op.drop_column("menus", "pinned_meal_id")
    op.drop_column("menus", "pin_dismissed")

    menuorigin_enum = postgresql.ENUM("suggestion", "manual", "pinned", name="menuorigin")
    menuorigin_enum.create(op.get_bind(), checkfirst=True)

    menustate_enum = postgresql.ENUM("suggestion", "eaten", name="menustate")
    menustate_enum.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "menus",
        sa.Column(
            "origin",
            menuorigin_enum,
            server_default="manual",
            nullable=False,
        ),
    )
    op.add_column(
        "menus",
        sa.Column(
            "state",
            menustate_enum,
            server_default="eaten",
            nullable=False,
        ),
    )

    # 7. Reshape nutrition_profiles
    op.drop_column("nutrition_profiles", "meals")
    op.add_column(
        "nutrition_profiles",
        sa.Column(
            "weight_variety",
            sa.Float(),
            server_default=sa.text("0.5"),
            nullable=False,
        ),
    )
    op.add_column(
        "nutrition_profiles",
        sa.Column(
            "weight_balance",
            sa.Float(),
            server_default=sa.text("0.5"),
            nullable=False,
        ),
    )
    op.add_column(
        "nutrition_profiles",
        sa.Column(
            "weight_goal",
            sa.Float(),
            server_default=sa.text("0.5"),
            nullable=False,
        ),
    )

    # 8. Reshape menu_day_evaluations
    op.add_column("menu_day_evaluations", sa.Column("menu_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_menu_day_evaluations_menu_id_menus",
        "menu_day_evaluations",
        "menus",
        ["menu_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_menu_day_evaluations_menu_id",
        "menu_day_evaluations",
        ["menu_id"],
        unique=False,
    )

    # 9. Reshape generation_log
    op.add_column("generation_log", sa.Column("menu_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_generation_log_menu_id_menus",
        "generation_log",
        "menus",
        ["menu_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_generation_log_menu_id",
        "generation_log",
        ["menu_id"],
        unique=False,
    )

    # 10. Drop unused enums
    sa.Enum(name="productavailability").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="productpreference").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="menunotekind").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="dishkind").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="menustatus").drop(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    # 1. generation_log
    op.drop_index("ix_generation_log_menu_id", table_name="generation_log")
    op.drop_constraint("fk_generation_log_menu_id_menus", "generation_log", type_="foreignkey")
    op.drop_column("generation_log", "menu_id")

    # 2. menu_day_evaluations
    op.drop_index("ix_menu_day_evaluations_menu_id", table_name="menu_day_evaluations")
    op.drop_constraint(
        "fk_menu_day_evaluations_menu_id_menus",
        "menu_day_evaluations",
        type_="foreignkey",
    )
    op.drop_column("menu_day_evaluations", "menu_id")

    # 3. nutrition_profiles
    op.drop_column("nutrition_profiles", "weight_goal")
    op.drop_column("nutrition_profiles", "weight_balance")
    op.drop_column("nutrition_profiles", "weight_variety")
    op.add_column(
        "nutrition_profiles",
        sa.Column(
            "meals",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )

    # 4. menus
    op.drop_column("menus", "state")
    op.drop_column("menus", "origin")
    sa.Enum(name="menustate").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="menuorigin").drop(op.get_bind(), checkfirst=True)

    op.add_column(
        "menus",
        sa.Column(
            "pin_dismissed",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column("menus", sa.Column("pinned_meal_id", sa.Uuid(), nullable=True))
    op.add_column(
        "menus",
        sa.Column(
            "removed",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        "menus",
        sa.Column(
            "regenerations",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.add_column("menus", sa.Column("time", sa.String(), nullable=True))
    op.add_column(
        "menus",
        sa.Column(
            "date",
            sa.Date(),
            server_default=sa.text("CURRENT_DATE"),
            nullable=False,
        ),
    )
    op.create_index("ix_menus_date", "menus", ["date"], unique=False)
    op.create_unique_constraint(
        "uq_menus_user_id_date_meal_type",
        "menus",
        ["user_id", "date", "meal_type"],
    )

    # 5. dishes
    op.drop_column("dishes", "note")
    op.drop_column("dishes", "nutrition_overridden")
    op.drop_column("dishes", "pinned_meal_type")
    op.drop_column("dishes", "is_ready")

    op.add_column("dishes", sa.Column("notes", sa.String(), nullable=True))
    op.add_column("dishes", sa.Column("cooked_on", sa.Date(), nullable=True))
    op.add_column("dishes", sa.Column("remaining_g", sa.Integer(), nullable=True))
    op.add_column("dishes", sa.Column("cooked_weight_g", sa.Integer(), nullable=True))
    op.add_column("dishes", sa.Column("recipe_id", sa.Uuid(), nullable=True))
    op.create_check_constraint(
        "chk_dishes_cooked_weight_g_positive",
        "dishes",
        "cooked_weight_g > 0",
    )
    op.create_check_constraint(
        "chk_dishes_remaining_g_non_negative",
        "dishes",
        "(remaining_g IS NULL) OR (remaining_g >= 0)",
    )
    op.create_index("ix_dishes_recipe_id", "dishes", ["recipe_id"], unique=False)

    # 6. Recreate enums for products and notes
    productavailability = postgresql.ENUM(
        "in_stock", "low", "out", name="productavailability", create_type=False
    )
    productavailability.create(op.get_bind(), checkfirst=True)

    productpreference = postgresql.ENUM(
        "preferred", "neutral", "avoid", name="productpreference", create_type=False
    )
    productpreference.create(op.get_bind(), checkfirst=True)

    menunotekind = postgresql.ENUM("info", "correction", name="menunotekind", create_type=False)
    menunotekind.create(op.get_bind(), checkfirst=True)

    # 7. products
    op.drop_column("products", "note")
    op.drop_column("products", "available")
    op.add_column("products", sa.Column("sodium_mg", sa.Float(), nullable=True))
    op.add_column("products", sa.Column("sugar_g", sa.Float(), nullable=True))
    op.add_column("products", sa.Column("fiber_g", sa.Float(), nullable=True))
    op.add_column("products", sa.Column("carbs_g", sa.Float(), nullable=True))
    op.add_column("products", sa.Column("fat_g", sa.Float(), nullable=True))
    op.add_column("products", sa.Column("protein_g", sa.Float(), nullable=True))
    op.add_column("products", sa.Column("kcal", sa.Float(), nullable=True))
    op.add_column("products", sa.Column("stock_g", sa.Integer(), nullable=True))
    op.add_column("products", sa.Column("count", sa.Integer(), nullable=True))
    op.add_column(
        "products",
        sa.Column(
            "eat_raw",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
    )
    op.add_column(
        "products",
        sa.Column(
            "preference",
            productpreference,
            server_default="neutral",
            nullable=False,
        ),
    )
    op.add_column(
        "products",
        sa.Column(
            "availability",
            productavailability,
            server_default="in_stock",
            nullable=False,
        ),
    )

    # 8. Recreate dropped tables
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

    op.create_table(
        "menu_notes",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("uuidv7()"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("kind", menunotekind, nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_menu_notes_date"), "menu_notes", ["date"], unique=False)
    op.create_index(op.f("ix_menu_notes_user_id"), "menu_notes", ["user_id"], unique=False)

    op.create_table(
        "off_plan_entries",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("uuidv7()"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("text", sa.String(), nullable=False),
        sa.Column("kcal_est", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_off_plan_entries_date"),
        "off_plan_entries",
        ["date"],
        unique=False,
    )
    op.create_index(
        op.f("ix_off_plan_entries_user_id"),
        "off_plan_entries",
        ["user_id"],
        unique=False,
    )

    mealtype_enum = postgresql.ENUM(
        "breakfast",
        "lunch",
        "dinner",
        "morning_snack",
        "afternoon_snack",
        "evening_snack",
        name="mealtype",
        create_type=False,
    )

    op.create_table(
        "pinned_meals",
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
            nullable=False,
            index=True,
        ),
        sa.Column("meal_type", mealtype_enum, nullable=False),
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
        sa.UniqueConstraint("user_id", "meal_type", name="uq_pinned_meals_user_id_meal_type"),
    )

    op.create_foreign_key(
        "fk_menus_pinned_meal_id_pinned_meals",
        "menus",
        "pinned_meals",
        ["pinned_meal_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "recipes",
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
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column(
            "archived",
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
        sa.UniqueConstraint("user_id", "name", name="uq_recipes_user_id_name"),
    )

    op.create_foreign_key(
        "fk_dishes_recipe_id_recipes",
        "dishes",
        "recipes",
        ["recipe_id"],
        ["id"],
        ondelete="RESTRICT",
    )

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

    op.create_table(
        "frequent_dishes",
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

    op.create_table(
        "frequent_dish_components",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("uuidv7()"),
            primary_key=True,
            nullable=False,
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

    op.create_table(
        "pinned_meal_items",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("uuidv7()"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "pinned_meal_id",
            sa.Uuid(),
            sa.ForeignKey("pinned_meals.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "frequent_dish_id",
            sa.Uuid(),
            sa.ForeignKey("frequent_dishes.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "position",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
