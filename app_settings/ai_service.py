import logging
import time
from typing import Any

import httpx

import gemini_infra
from app_settings import ai_config
from app_settings.schemas import AiTestResult, GeminiModelInfo
from config import Settings
from gemini_infra.types import GeminiInvalidResponse, GeminiUnavailable

logger = logging.getLogger(__name__)

GEMINI_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"

_TEST_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {"ok": {"type": "BOOLEAN"}},
    "required": ["ok"],
}


class GeminiModelsUnavailable(Exception):
    pass


async def list_generate_models(settings: Settings) -> list[GeminiModelInfo]:
    headers = {"x-goog-api-key": settings.gemini_api_key}
    models: list[GeminiModelInfo] = []
    page_token: str | None = None
    try:
        async with httpx.AsyncClient(timeout=ai_config.timeout_seconds()) as client:
            while True:
                params: dict[str, Any] = {"pageSize": 1000}
                if page_token:
                    params["pageToken"] = page_token
                response = await client.get(GEMINI_MODELS_URL, headers=headers, params=params)
                if response.status_code != 200:
                    logger.error("Gemini models list returned status %d", response.status_code)
                    raise GeminiModelsUnavailable(
                        f"Gemini returned status {response.status_code} for the models list"
                    )
                data = response.json()
                for item in data.get("models", []):
                    if "generateContent" not in item.get("supportedGenerationMethods", []):
                        continue
                    name = str(item["name"])
                    models.append(
                        GeminiModelInfo(
                            name=name.removeprefix("models/"),
                            display_name=item.get("displayName", name),
                            input_token_limit=item.get("inputTokenLimit"),
                            output_token_limit=item.get("outputTokenLimit"),
                        )
                    )
                page_token = data.get("nextPageToken")
                if not page_token:
                    break
    except httpx.HTTPError as exc:
        logger.error("Gemini models list connection error: %s", exc)
        raise GeminiModelsUnavailable("Gemini is unreachable") from exc
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise GeminiModelsUnavailable("Unexpected response from Gemini models list") from exc
    return models


async def run_connection_test(settings: Settings, model: str | None) -> AiTestResult:
    target_model = model or ai_config.analyze_model()
    start = time.perf_counter()
    error: str | None = None
    try:
        await gemini_infra.generate_json(
            prompt='Reply with the JSON object {"ok": true}.',
            model=target_model,
            settings=settings,
            response_schema=_TEST_SCHEMA,
            temperature=0,
        )
    except GeminiUnavailable:
        error = "Gemini is unavailable, timed out or rejected the request"
    except GeminiInvalidResponse as exc:
        error = f"Invalid Gemini response: {exc}"
    latency_ms = int(round((time.perf_counter() - start) * 1000))
    return AiTestResult(ok=error is None, model=target_model, latency_ms=latency_ms, error=error)
