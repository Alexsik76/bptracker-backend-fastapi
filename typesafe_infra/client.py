import json
import logging
from typing import Any

import httpx

from app_settings import ai_config
from config import Settings
from typesafe_infra.types import (
    JevChoiceAnswer,
    JevInvalidResponse,
    JevResult,
    JevUnavailable,
    JevUsage,
)

logger = logging.getLogger(__name__)


async def evaluate_systemone(
    *,
    state: dict[str, Any],
    questions: dict[str, Any],
    settings: Settings,
    model: str | None = None,
    timeout_seconds: float | None = None,
) -> JevResult:
    """Send evaluation request to TypeSafe JEV SystemOne API."""
    if model is None:
        model = ai_config.jev_model()
    if timeout_seconds is None:
        timeout_seconds = float(ai_config.jev_timeout_seconds())

    url = f"{settings.typesafe_base_url.rstrip('/')}/v1/systemone"
    headers = {
        "Authorization": f"Bearer {settings.typesafe_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "state": state,
        "questions": questions,
    }

    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.post(url, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        logger.error("JEV connection error or timeout: %s", exc)
        raise JevUnavailable() from exc

    if response.status_code in (400, 422):
        detail: str | None = None
        try:
            err_json = response.json()
            if isinstance(err_json, dict) and "detail" in err_json:
                detail = str(err_json["detail"])
        except Exception:
            pass
        if detail:
            msg = f"JEV error {response.status_code}: {detail}"
        else:
            msg = f"JEV error {response.status_code}"
        logger.error("JEV returned %d: %s", response.status_code, response.text)
        raise JevInvalidResponse(msg, raw_text=response.text, status_code=response.status_code)

    if response.status_code != 200:
        logger.error(
            "JEV returned non-200 status code %d: %s",
            response.status_code,
            response.text,
        )
        raise JevUnavailable(f"JEV returned non-200 status code {response.status_code}")

    try:
        response_data = response.json()
    except (ValueError, json.JSONDecodeError) as exc:
        logger.warning("Failed to decode JEV response JSON body: %s", response.text)
        raise JevInvalidResponse(
            "JEV response body was not valid JSON",
            raw_text=response.text,
            status_code=response.status_code,
        ) from exc

    if not isinstance(response_data, dict) or "answers" not in response_data:
        logger.warning("Missing 'answers' in JEV response: %s", response.text)
        raise JevInvalidResponse(
            "Missing 'answers' in JEV response",
            raw_text=response.text,
            status_code=response.status_code,
        )

    raw_answers = response_data.get("answers", {})
    if not isinstance(raw_answers, dict):
        raise JevInvalidResponse(
            "JEV 'answers' must be a dictionary",
            raw_text=response.text,
            status_code=response.status_code,
        )

    parsed_answers: dict[str, JevChoiceAnswer] = {}
    for q_key, ans_dict in raw_answers.items():
        if not isinstance(ans_dict, dict):
            continue
        parsed_answers[q_key] = JevChoiceAnswer(
            choice=str(ans_dict.get("choice", "")),
            confidence=float(ans_dict.get("confidence", 0.0)),
            probabilities={
                k: float(v) for k, v in ans_dict.get("probabilities", {}).items()
            } if isinstance(ans_dict.get("probabilities"), dict) else {},
            type=str(ans_dict.get("type", "choice")),
        )

    usage: JevUsage | None = None
    raw_usage = response_data.get("usage")
    if isinstance(raw_usage, dict):
        usage = JevUsage(
            input_tokens=raw_usage.get("input_tokens"),
            output_tokens=raw_usage.get("output_tokens"),
        )

    return JevResult(
        model=response_data.get("model", model),
        answers=parsed_answers,
        usage=usage,
        raw_response=response.text,
    )
