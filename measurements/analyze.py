import base64
import json
import logging

import httpx
from pydantic import ValidationError
from sqlmodel import SQLModel

from config import Settings
from measurements.models import MeasurementBase

logger = logging.getLogger(__name__)


class GeminiUnavailable(Exception):
    """Raised when the Gemini API is unreachable, times out, or returns a non-2xx status."""

    pass


class RecognitionFailed(Exception):
    """Raised when the Gemini response cannot be parsed, has missing fields, or is out of range."""

    pass


class AnalyzeResult(SQLModel):
    sys: int
    dia: int
    pulse: int


async def analyze_image(
    image_bytes: bytes,
    content_type: str,
    settings: Settings,
) -> AnalyzeResult:
    b64_data = base64.b64encode(image_bytes).decode("utf-8")

    prompt = (
        "Analyze this blood pressure monitor image. Extract: 1. Systolic (top number) "
        "2. Diastolic (middle number) 3. Pulse (bottom number). Return ONLY a valid JSON object "
        "with keys: 'systolic', 'diastolic', 'pulse'. No markdown, no comments."
    )

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{settings.gemini_model}:generateContent"
    )
    headers = {
        "x-goog-api-key": settings.gemini_api_key,
    }

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                    {
                        "inlineData": {
                            "mimeType": content_type,
                            "data": b64_data,
                        }
                    },
                ]
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.1,
        },
    }

    try:
        async with httpx.AsyncClient(timeout=settings.gemini_timeout_seconds) as client:
            response = await client.post(url, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        logger.error("Gemini connection error or timeout: %s", exc)
        raise GeminiUnavailable() from exc

    if response.status_code != 200:
        logger.error("Gemini returned non-200 status code: %d.", response.status_code)
        raise GeminiUnavailable()

    try:
        response_data = response.json()
        candidate = response_data["candidates"][0]
        part = candidate["content"]["parts"][0]
        text_response = part["text"]
    except (KeyError, IndexError, ValueError) as exc:
        logger.warning("Failed to parse Gemini response structure: %s", response.text)
        raise RecognitionFailed() from exc

    # Clean code fences if generated anyway
    text_response = text_response.strip()
    if text_response.startswith("```"):
        lines = text_response.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text_response = "\n".join(lines).strip()

    try:
        parsed = json.loads(text_response)
        systolic = int(parsed["systolic"])
        diastolic = int(parsed["diastolic"])
        pulse = int(parsed["pulse"])
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        logger.warning(
            "Gemini output was not valid JSON or missing expected keys: %s", text_response
        )
        raise RecognitionFailed() from exc

    # Validate ranges through shared Pydantic bounds on MeasurementBase
    try:
        MeasurementBase(sys=systolic, dia=diastolic, pulse=pulse)
    except ValidationError as exc:
        logger.warning("Recognised values failed Pydantic validation: %s", exc)
        raise RecognitionFailed() from exc

    return AnalyzeResult(sys=systolic, dia=diastolic, pulse=pulse)
