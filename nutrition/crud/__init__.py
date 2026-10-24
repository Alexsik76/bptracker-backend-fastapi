from nutrition.crud.dish import (
    create_dish,
    delete_dish,
    get_dish,
    get_dishes,
    update_dish,
)
from nutrition.crud.menu import (
    add_generation_log,
    add_menu_item,
    delete_menu_item,
    evaluate_active_menus_completeness,
    get_day_menus,
    get_menu,
    get_menu_by_id,
    get_or_create_menu,
    replace_unmarked_slot_items,
    update_menu,
    update_menu_item,
)
from nutrition.crud.notes import create_menu_note, get_menu_notes
from nutrition.crud.off_plan import (
    create_off_plan_entry,
    delete_off_plan_entry,
    get_off_plan_entries,
)
from nutrition.crud.product import (
    create_product,
    delete_product,
    get_available_products,
    get_product,
    get_products,
    update_product,
)
from nutrition.crud.profile import get_nutrition_profile, upsert_nutrition_profile

__all__ = [
    "create_menu_note",
    "get_menu_notes",
    "create_off_plan_entry",
    "get_off_plan_entries",
    "delete_off_plan_entry",
    "get_nutrition_profile",
    "upsert_nutrition_profile",
    "create_product",
    "get_products",
    "get_available_products",
    "get_product",
    "update_product",
    "delete_product",
    "create_dish",
    "get_dishes",
    "get_dish",
    "update_dish",
    "delete_dish",
    "get_day_menus",
    "get_menu",
    "get_menu_by_id",
    "get_or_create_menu",
    "update_menu",
    "add_menu_item",
    "update_menu_item",
    "delete_menu_item",
    "replace_unmarked_slot_items",
    "add_generation_log",
    "evaluate_active_menus_completeness",
]
