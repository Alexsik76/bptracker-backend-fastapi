from app_settings import cache


def menu_history_days() -> int:
    return cache.get_value("nutrition.menu_history_days")
