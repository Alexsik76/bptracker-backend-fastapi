from app_settings import cache


def analyze_model() -> str:
    return cache.get_value("ai.gemini.analyze_model")


def menu_model() -> str:
    return cache.get_value("ai.gemini.menu_model")


def timeout_seconds() -> int:
    return cache.get_value("ai.gemini.timeout_seconds")


def llm_exchange_log() -> bool:
    return cache.get_value("ai.llm_exchange_log")


def jev_model() -> str:
    return cache.get_value("ai.jev.model")


def jev_timeout_seconds() -> int:
    return cache.get_value("ai.jev.timeout_seconds")


def jev_min_confidence_percent() -> int:
    return cache.get_value("ai.jev.min_confidence_percent")
