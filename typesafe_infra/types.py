from dataclasses import dataclass


@dataclass(frozen=True)
class JevChoiceAnswer:
    choice: str
    confidence: float
    probabilities: dict[str, float]
    type: str = "choice"


@dataclass(frozen=True)
class JevUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(frozen=True)
class JevResult:
    model: str
    answers: dict[str, JevChoiceAnswer]
    usage: JevUsage | None = None
    raw_response: str = ""


class JevUnavailable(Exception):
    """Raised when the JEV API is unreachable, times out, or returns an unexpected status."""


class JevInvalidResponse(Exception):
    """Raised when JEV returns 400/422 or the response structure is invalid JSON/schema."""

    def __init__(
        self,
        message: str = "",
        *,
        raw_text: str | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.raw_text = raw_text
        self.status_code = status_code
