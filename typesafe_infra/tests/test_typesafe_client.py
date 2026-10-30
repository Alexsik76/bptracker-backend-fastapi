from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from config import get_settings
from typesafe_infra import (
    JevChoiceAnswer,
    JevInvalidResponse,
    JevResult,
    JevUnavailable,
    evaluate_systemone,
)


@pytest.mark.asyncio
async def test_evaluate_systemone_happy_path():
    settings = get_settings()
    settings_with_key = settings.model_copy(update={"typesafe_api_key": "test-key-123"})
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.text = (
        '{"model": "jev-latest", "answers": {"goal": {"type": "choice", "choice": "green", '
        '"confidence": 0.85, "probabilities": {"green": 0.85, "yellow": 0.1, "red": 0.05}}}, '
        '"usage": {"input_tokens": 120, "output_tokens": 12}}'
    )
    mock_response.json.return_value = {
        "model": "jev-latest",
        "answers": {
            "goal": {
                "type": "choice",
                "choice": "green",
                "confidence": 0.85,
                "probabilities": {
                    "green": 0.85,
                    "yellow": 0.1,
                    "red": 0.05,
                },
            }
        },
        "usage": {
            "input_tokens": 120,
            "output_tokens": 12,
        },
    }

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response

    state = {"user_goal": {"daily_kcal_norm": 2000}, "day_menu": {"total_kcal": 2050}}
    questions = {
        "goal": {
            "type": "choice",
            "instructions": "Evaluate kcal match",
            "criteria": {"green": "ok", "yellow": "medium", "red": "bad"},
        }
    }

    with patch("typesafe_infra.client.httpx.AsyncClient") as mock_client_class:
        mock_client_class.return_value.__aenter__.return_value = mock_client

        result = await evaluate_systemone(
            state=state,
            questions=questions,
            settings=settings_with_key,
            model="jev-latest",
        )

        assert isinstance(result, JevResult)
        assert result.model == "jev-latest"
        assert "goal" in result.answers
        ans = result.answers["goal"]
        assert isinstance(ans, JevChoiceAnswer)
        assert ans.choice == "green"
        assert ans.confidence == 0.85
        assert ans.probabilities == {"green": 0.85, "yellow": 0.1, "red": 0.05}
        assert result.usage is not None
        assert result.usage.input_tokens == 120
        assert result.usage.output_tokens == 12

        mock_client.post.assert_awaited_once()
        args, kwargs = mock_client.post.await_args
        assert args[0] == f"{settings.typesafe_base_url.rstrip('/')}/v1/systemone"
        assert kwargs["headers"]["Authorization"] == "Bearer test-key-123"
        sent_payload = kwargs["json"]
        assert sent_payload["model"] == "jev-latest"
        assert sent_payload["state"] == state
        assert sent_payload["questions"] == questions


@pytest.mark.asyncio
async def test_evaluate_systemone_timeout():
    settings = get_settings().model_copy(update={"typesafe_api_key": "test-key-123"})
    mock_client = AsyncMock()
    mock_client.post.side_effect = httpx.ConnectTimeout("Connection timed out")

    with patch("typesafe_infra.client.httpx.AsyncClient") as mock_client_class:
        mock_client_class.return_value.__aenter__.return_value = mock_client

        with pytest.raises(JevUnavailable):
            await evaluate_systemone(
                state={},
                questions={},
                settings=settings,
            )


@pytest.mark.asyncio
async def test_evaluate_systemone_400_bad_request():
    settings = get_settings().model_copy(update={"typesafe_api_key": "test-key-123"})
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 400
    mock_response.text = '{"detail": "Invalid question format"}'
    mock_response.json.return_value = {"detail": "Invalid question format"}

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response

    with patch("typesafe_infra.client.httpx.AsyncClient") as mock_client_class:
        mock_client_class.return_value.__aenter__.return_value = mock_client

        with pytest.raises(JevInvalidResponse) as exc_info:
            await evaluate_systemone(
                state={},
                questions={},
                settings=settings,
            )
        assert exc_info.value.status_code == 400
        assert "Invalid question format" in str(exc_info.value)


@pytest.mark.asyncio
async def test_evaluate_systemone_422_unprocessable_entity():
    settings = get_settings().model_copy(update={"typesafe_api_key": "test-key-123"})
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 422
    mock_response.text = '{"detail": "Validation error in criteria"}'
    mock_response.json.return_value = {"detail": "Validation error in criteria"}

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response

    with patch("typesafe_infra.client.httpx.AsyncClient") as mock_client_class:
        mock_client_class.return_value.__aenter__.return_value = mock_client

        with pytest.raises(JevInvalidResponse) as exc_info:
            await evaluate_systemone(
                state={},
                questions={},
                settings=settings,
            )
        assert exc_info.value.status_code == 422
        assert "Validation error in criteria" in str(exc_info.value)


@pytest.mark.asyncio
async def test_evaluate_systemone_500_server_error():
    settings = get_settings().model_copy(update={"typesafe_api_key": "test-key-123"})
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 500
    mock_response.text = "Internal Server Error"

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response

    with patch("typesafe_infra.client.httpx.AsyncClient") as mock_client_class:
        mock_client_class.return_value.__aenter__.return_value = mock_client

        with pytest.raises(JevUnavailable):
            await evaluate_systemone(
                state={},
                questions={},
                settings=settings,
            )


@pytest.mark.asyncio
async def test_evaluate_systemone_invalid_json():
    settings = get_settings().model_copy(update={"typesafe_api_key": "test-key-123"})
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.text = "Not JSON"
    mock_response.json.side_effect = ValueError("Invalid JSON")

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response

    with patch("typesafe_infra.client.httpx.AsyncClient") as mock_client_class:
        mock_client_class.return_value.__aenter__.return_value = mock_client

        with pytest.raises(JevInvalidResponse):
            await evaluate_systemone(
                state={},
                questions={},
                settings=settings,
            )


@pytest.mark.asyncio
async def test_evaluate_systemone_missing_answers():
    settings = get_settings().model_copy(update={"typesafe_api_key": "test-key-123"})
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.text = '{"model": "jev-latest"}'
    mock_response.json.return_value = {"model": "jev-latest"}

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response

    with patch("typesafe_infra.client.httpx.AsyncClient") as mock_client_class:
        mock_client_class.return_value.__aenter__.return_value = mock_client

        with pytest.raises(JevInvalidResponse):
            await evaluate_systemone(
                state={},
                questions={},
                settings=settings,
            )
