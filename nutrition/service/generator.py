import logging
from datetime import date
from uuid import UUID

from app_settings import ai_config
from config import Settings

logger = logging.getLogger(__name__)
llm_logger = logging.getLogger("nutrition.llm")


def log_llm_exchange(
    settings: Settings,
    user_id: UUID,
    menu_date: date,
    scope: str,
    model: str,
    prompt_version: str,
    duration_ms: int | None,
    input_tokens: int | None,
    output_tokens: int | None,
    prompt: str,
    raw_response: str | None,
) -> None:
    """Emit full LLM prompt and response exchange record to nutrition.llm at INFO."""
    if not ai_config.llm_exchange_log():
        return
    llm_logger.info(
        "user_id=%s date=%s scope=%s model=%s prompt_version=%s duration_ms=%s "
        "tokens=(input=%s, output=%s)\nPROMPT:\n%s\nRAW RESPONSE:\n%s",
        user_id,
        menu_date,
        scope,
        model,
        prompt_version,
        duration_ms,
        input_tokens,
        output_tokens,
        prompt,
        raw_response,
    )


class GenerationError(Exception):
    """Base exception for generation service errors."""


class InvalidGenerationRequest(GenerationError):
    """Client request parameters are invalid."""


class ProfileNotFoundError(GenerationError):
    """Nutrition profile not found."""


class NothingToGenerateError(GenerationError):
    """No generatable items found."""


class ServiceGeminiUnavailable(GenerationError):
    """Gemini API is unavailable or timed out."""


class ServiceGenerationInvalidResponse(GenerationError):
    """Gemini returned invalid or schema-violating output."""

    def __init__(self, message: str, raw_response: str | None = None) -> None:
        super().__init__(message)
        self.raw_response = raw_response
