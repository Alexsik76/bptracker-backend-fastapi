"""use meal_type enum instead of free-text slot

Revision ID: c7b8a9d0e1f2
Revises: b9e8d7c6b5a4
Create Date: 2026-10-27 13:00:00.000000

"""

import json
import re
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7b8a9d0e1f2"
down_revision: str | Sequence[str] | None = "b9e8d7c6b5a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _map_name(name: str | None) -> str | None:
    if not name:
        return None
    clean = re.sub(r"\s*\(\d{1,2}:\d{2}\)\s*$", "", name).strip().lower()
    mapping = {
        "сніданок": "breakfast",
        "обід": "lunch",
        "вечеря": "dinner",
        "breakfast": "breakfast",
        "lunch": "lunch",
        "dinner": "dinner",
        "morning_snack": "morning_snack",
        "afternoon_snack": "afternoon_snack",
        "evening_snack": "evening_snack",
    }
    return mapping.get(clean)


def upgrade() -> None:
    conn = op.get_bind()

    # 1. Delete all menu_items and menus rows
    op.execute("DELETE FROM menu_items")
    op.execute("DELETE FROM menus")

    # 2. Create PostgreSQL enum 'mealtype'
    mealtype_enum = postgresql.ENUM(
        "breakfast",
        "lunch",
        "dinner",
        "morning_snack",
        "afternoon_snack",
        "evening_snack",
        name="mealtype",
    )
    mealtype_enum.create(conn, checkfirst=True)

    # 3. Convert nutrition_profiles meals JSON:
    # Drop unmapped entries, map known names, keep unique types sorted by time
    rows = conn.execute(sa.text("SELECT user_id, meals FROM nutrition_profiles")).fetchall()
    for user_id, meals in rows:
        if not meals or not isinstance(meals, list):
            continue
        new_meals = []
        seen_types = set()
        for item in meals:
            if not isinstance(item, dict):
                continue
            name = item.get("name") or item.get("type") or item.get("slot")
            mapped = _map_name(name)
            if mapped and mapped not in seen_types:
                seen_types.add(mapped)
                new_meals.append({"type": mapped, "time": item.get("time", "")})
        new_meals.sort(key=lambda m: m.get("time", ""))
        conn.execute(
            sa.text("UPDATE nutrition_profiles SET meals = :meals WHERE user_id = :uid"),
            {"meals": json.dumps(new_meals), "uid": user_id},
        )

    # 4. Convert pinned_meals.slot -> meal_type:
    # Unmapped pinned meals deleted; if duplicate types exist for a user, keep first and delete rest
    pin_rows = conn.execute(sa.text("SELECT id, user_id, slot FROM pinned_meals")).fetchall()
    seen_user_pins: set[tuple[object, str]] = set()
    for pin_id, user_id, slot in pin_rows:
        mapped = _map_name(slot)
        if not mapped:
            conn.execute(sa.text("DELETE FROM pinned_meals WHERE id = :pid"), {"pid": pin_id})
        else:
            key = (user_id, mapped)
            if key in seen_user_pins:
                conn.execute(sa.text("DELETE FROM pinned_meals WHERE id = :pid"), {"pid": pin_id})
            else:
                seen_user_pins.add(key)
                conn.execute(
                    sa.text("UPDATE pinned_meals SET slot = :mapped WHERE id = :pid"),
                    {"mapped": mapped, "pid": pin_id},
                )

    op.drop_constraint("uq_pinned_meals_user_id_slot", "pinned_meals", type_="unique")
    op.alter_column(
        "pinned_meals",
        "slot",
        new_column_name="meal_type",
        type_=mealtype_enum,
        postgresql_using="slot::mealtype",
        nullable=False,
    )
    op.create_unique_constraint(
        "uq_pinned_meals_user_id_meal_type",
        "pinned_meals",
        ["user_id", "meal_type"],
    )

    # 5. Convert dishes.fixed_slot -> fixed_meal_type:
    # Unmapped fixed slots NULL
    dish_rows = conn.execute(
        sa.text("SELECT id, fixed_slot FROM dishes WHERE fixed_slot IS NOT NULL")
    ).fetchall()
    for dish_id, fixed_slot in dish_rows:
        mapped = _map_name(fixed_slot)
        conn.execute(
            sa.text("UPDATE dishes SET fixed_slot = :mapped WHERE id = :did"),
            {"mapped": mapped, "did": dish_id},
        )

    op.alter_column(
        "dishes",
        "fixed_slot",
        new_column_name="fixed_meal_type",
        type_=mealtype_enum,
        postgresql_using="fixed_slot::mealtype",
        nullable=True,
    )

    # 6. Alter menus table (already emptied):
    op.drop_constraint("uq_menus_user_id_date_slot", "menus", type_="unique")
    op.alter_column(
        "menus",
        "slot",
        new_column_name="meal_type",
        type_=mealtype_enum,
        postgresql_using="slot::mealtype",
        nullable=False,
    )
    op.create_unique_constraint(
        "uq_menus_user_id_date_meal_type",
        "menus",
        ["user_id", "date", "meal_type"],
    )


def downgrade() -> None:
    conn = op.get_bind()

    # 1. Menus: meal_type -> slot
    op.drop_constraint("uq_menus_user_id_date_meal_type", "menus", type_="unique")
    op.alter_column(
        "menus",
        "meal_type",
        new_column_name="slot",
        type_=sa.String(),
        postgresql_using="meal_type::text",
        nullable=False,
    )
    op.create_unique_constraint(
        "uq_menus_user_id_date_slot",
        "menus",
        ["user_id", "date", "slot"],
    )

    # 2. Dishes: fixed_meal_type -> fixed_slot
    op.alter_column(
        "dishes",
        "fixed_meal_type",
        new_column_name="fixed_slot",
        type_=sa.String(),
        postgresql_using="fixed_meal_type::text",
        nullable=True,
    )

    # 3. Pinned meals: meal_type -> slot
    op.drop_constraint("uq_pinned_meals_user_id_meal_type", "pinned_meals", type_="unique")
    op.alter_column(
        "pinned_meals",
        "meal_type",
        new_column_name="slot",
        type_=sa.String(),
        postgresql_using="meal_type::text",
        nullable=False,
    )
    op.create_unique_constraint(
        "uq_pinned_meals_user_id_slot",
        "pinned_meals",
        ["user_id", "slot"],
    )

    # 4. Nutrition profiles: convert type back to name
    rows = conn.execute(sa.text("SELECT user_id, meals FROM nutrition_profiles")).fetchall()
    for user_id, meals in rows:
        if not meals or not isinstance(meals, list):
            continue
        old_meals = []
        for item in meals:
            if isinstance(item, dict):
                old_meals.append({
                    "name": item.get("type", ""),
                    "time": item.get("time", ""),
                })
        conn.execute(
            sa.text("UPDATE nutrition_profiles SET meals = :meals WHERE user_id = :uid"),
            {"meals": json.dumps(old_meals), "uid": user_id},
        )

    # 5. Drop enum 'mealtype'
    mealtype_enum = postgresql.ENUM(
        "breakfast",
        "lunch",
        "dinner",
        "morning_snack",
        "afternoon_snack",
        "evening_snack",
        name="mealtype",
    )
    mealtype_enum.drop(conn, checkfirst=True)
