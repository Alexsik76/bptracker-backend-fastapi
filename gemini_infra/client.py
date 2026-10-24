import base64
import json
import logging
from collections.abc import Sequence
from typing import Any

import httpx

from config import Settings
from gemini_infra.types import (
    GeminiAttachment,
    GeminiInvalidResponse,
    GeminiResult,
    GeminiUnavailable,
)

logger = logging.getLogger(__name__)


def _strip_code_fences(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    return cleaned


async def generate_json(
    *,
    prompt: str,
    model: str,
    settings: Settings,
    response_schema: dict[str, Any] | None = None,
    attachments: Sequence[GeminiAttachment] = (),
    temperature: float | None = None,
) -> GeminiResult:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    headers = {
        "x-goog-api-key": settings.gemini_api_key,
    }

    parts: list[dict[str, Any]] = [{"text": prompt}]
    for attachment in attachments:
        b64_data = base64.b64encode(attachment.data).decode("utf-8")
        parts.append(
            {
                "inlineData": {
                    "mimeType": attachment.mime_type,
                    "data": b64_data,
                }
            }
        )

    generation_config: dict[str, Any] = {
        "responseMimeType": "application/json",
    }
    if response_schema is not None:
        generation_config["responseSchema"] = response_schema
    if temperature is not None:
        generation_config["temperature"] = temperature

    payload: dict[str, Any] = {
        "contents": [
            {
                "parts": parts,
            }
        ],
        "generationConfig": generation_config,
    }

    try:
        async with httpx.AsyncClient(timeout=settings.gemini_timeout_seconds) as client:
            response = await client.post(url, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        logger.error("Gemini connection error or timeout: %s", exc)
        raise GeminiUnavailable() from exc

    if response.status_code != 200:
        logger.error(
            "Gemini returned non-200 status code %d: %s",
            response.status_code,
            response.text,
        )
        raise GeminiUnavailable()

    try:
        response_data = response.json()
    except (ValueError, json.JSONDecodeError) as exc:
        logger.warning("Failed to decode Gemini response JSON body: %s", response.text)
        raise GeminiInvalidResponse(
            "Gemini response body was not valid JSON",
            raw_text=response.text,
        ) from exc

    raw_text: str | None = None
    finish_reason: str | None = None
    try:
        candidate = response_data["candidates"][0]
        finish_reason = candidate.get("finishReason") if isinstance(candidate, dict) else None
        part = candidate["content"]["parts"][0]
        raw_text = part["text"]
    except (KeyError, IndexError, TypeError) as exc:
        logger.warning(
            "Failed to extract text from Gemini response (finishReason=%s): %s",
            finish_reason,
            response.text,
        )
        raise GeminiInvalidResponse(
            "Missing candidates/parts/text in Gemini response",
            raw_text=response.text,
        ) from exc

    cleaned_text = _strip_code_fences(raw_text)

    try:
        parsed_data = json.loads(cleaned_text)
    except (ValueError, json.JSONDecodeError) as exc:
        logger.warning(
            "Gemini output was not valid JSON (finishReason=%s): %s",
            finish_reason,
            raw_text,
        )
        raise GeminiInvalidResponse(
            "Gemini output was not valid JSON",
            raw_text=raw_text,
        ) from exc

    input_tokens: int | None = None
    output_tokens: int | None = None
    if isinstance(response_data, dict):
        usage = response_data.get("usageMetadata")
        if isinstance(usage, dict):
            input_tokens = usage.get("promptTokenCount")
            output_tokens = usage.get("candidatesTokenCount")

    return GeminiResult(
        data=parsed_data,
        raw_text=raw_text,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
