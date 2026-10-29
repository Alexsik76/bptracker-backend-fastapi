from app_settings import cache


def analyze_model() -> str:
    return cache.get_value("ai.gemini.analyze_model")


def menu_model() -> str:
    return cache.get_value("ai.gemini.menu_model")


def timeout_seconds() -> int:
    return cache.get_value("ai.gemini.timeout_seconds")


def llm_exchange_log() -> bool:
    return cache.get_value("ai.llm_exchange_log")
