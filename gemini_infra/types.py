from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GeminiAttachment:
    mime_type: str
    data: bytes


@dataclass(frozen=True)
class GeminiResult:
    data: Any
    raw_text: str
    model: str
    input_tokens: int | None
    output_tokens: int | None


class GeminiUnavailable(Exception):
    """Raised when the Gemini API is unreachable, times out, or returns a non-2xx status."""


class GeminiInvalidResponse(Exception):
    """Raised when the Gemini response structure is missing parts or text is not valid JSON."""

    def __init__(self, message: str = "", *, raw_text: str | None = None) -> None:
        super().__init__(message)
        self.raw_text = raw_text
