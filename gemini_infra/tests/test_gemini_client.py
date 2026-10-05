import base64
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from config import get_settings
from gemini_infra import (
    GeminiAttachment,
    GeminiInvalidResponse,
    GeminiResult,
    GeminiUnavailable,
    generate_json,
)


@pytest.mark.asyncio
async def test_generate_json_happy_path():
    settings = get_settings()
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": '{"menu": ["oats", "tea"]}'}],
                },
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 25,
            "candidatesTokenCount": 15,
        },
    }

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response

    schema = {"type": "object", "properties": {"menu": {"type": "array"}}}
    attachment_data = b"sample photo or document bytes"
    attachment = GeminiAttachment(mime_type="image/jpeg", data=attachment_data)

    with patch("gemini_infra.client.httpx.AsyncClient") as mock_client_class:
        mock_client_class.return_value.__aenter__.return_value = mock_client

        result = await generate_json(
            prompt="Generate a healthy menu",
            model="gemini-2.0-flash",
            settings=settings,
            response_schema=schema,
            attachments=[attachment],
            temperature=0.7,
        )

        assert isinstance(result, GeminiResult)
        assert result.data == {"menu": ["oats", "tea"]}
        assert result.raw_text == '{"menu": ["oats", "tea"]}'
        assert result.model == "gemini-2.0-flash"
        assert result.input_tokens == 25
        assert result.output_tokens == 15

        # Verify outgoing request payload, headers, and URL
        mock_client.post.assert_awaited_once()
        args, kwargs = mock_client.post.await_args
        assert args[0] == (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            "gemini-2.0-flash:generateContent"
        )
        assert kwargs["headers"] == {"x-goog-api-key": settings.gemini_api_key}

        sent_payload = kwargs["json"]
        assert sent_payload["generationConfig"]["responseSchema"] == schema
        assert sent_payload["generationConfig"]["temperature"] == 0.7
        assert sent_payload["generationConfig"]["responseMimeType"] == "application/json"

        expected_parts = [
            {"text": "Generate a healthy menu"},
            {
                "inlineData": {
                    "mimeType": "image/jpeg",
                    "data": base64.b64encode(attachment_data).decode("utf-8"),
                }
            },
        ]
        assert sent_payload["contents"][0]["parts"] == expected_parts


@pytest.mark.asyncio
async def test_generate_json_code_fence_wrapped():
    settings = get_settings()
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    wrapped_text = "```json\n{\n  \"meal\": \"salad\"\n}\n```"
    mock_response.json.return_value = {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": wrapped_text}],
                },
            }
        ]
    }

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response

    with patch("gemini_infra.client.httpx.AsyncClient") as mock_client_class:
        mock_client_class.return_value.__aenter__.return_value = mock_client

        result = await generate_json(
            prompt="Generate a meal",
            model="gemini-2.0-flash",
            settings=settings,
        )

        assert result.data == {"meal": "salad"}
        assert result.raw_text == wrapped_text
        assert result.input_tokens is None
        assert result.output_tokens is None


@pytest.mark.asyncio
async def test_generate_json_timeout():
    settings = get_settings()
    mock_client = AsyncMock()
    mock_client.post.side_effect = httpx.ConnectTimeout("Connection timed out")

    with patch("gemini_infra.client.httpx.AsyncClient") as mock_client_class:
        mock_client_class.return_value.__aenter__.return_value = mock_client

        with pytest.raises(GeminiUnavailable):
            await generate_json(
                prompt="Generate a meal",
                model="gemini-2.0-flash",
                settings=settings,
            )


@pytest.mark.asyncio
async def test_generate_json_non_200():
    settings = get_settings()
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 500
    mock_response.text = "Internal Server Error"

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response

    with patch("gemini_infra.client.httpx.AsyncClient") as mock_client_class:
        mock_client_class.return_value.__aenter__.return_value = mock_client

        with pytest.raises(GeminiUnavailable):
            await generate_json(
                prompt="Generate a meal",
                model="gemini-2.0-flash",
                settings=settings,
            )


@pytest.mark.asyncio
async def test_generate_json_non_json_text():
    settings = get_settings()
    raw_invalid_text = "I am sorry, but I cannot fulfill this request."
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": raw_invalid_text}],
                },
            }
        ]
    }

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response

    with patch("gemini_infra.client.httpx.AsyncClient") as mock_client_class:
        mock_client_class.return_value.__aenter__.return_value = mock_client

        with pytest.raises(GeminiInvalidResponse) as exc_info:
            await generate_json(
                prompt="Generate a meal",
                model="gemini-2.0-flash",
                settings=settings,
            )

        assert exc_info.value.raw_text == raw_invalid_text


@pytest.mark.asyncio
async def test_generate_json_missing_candidates():
    settings = get_settings()
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.text = '{"candidates": []}'
    mock_response.json.return_value = {"candidates": []}

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response

    with patch("gemini_infra.client.httpx.AsyncClient") as mock_client_class:
        mock_client_class.return_value.__aenter__.return_value = mock_client

        with pytest.raises(GeminiInvalidResponse) as exc_info:
            await generate_json(
                prompt="Generate a meal",
                model="gemini-2.0-flash",
                settings=settings,
            )

        assert exc_info.value.raw_text == '{"candidates": []}'


@pytest.mark.asyncio
async def test_generate_json_omits_optional_schema_and_temperature():
    settings = get_settings()
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": '{"result": 1}'}]}}],
    }

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response

    with patch("gemini_infra.client.httpx.AsyncClient") as mock_client_class:
        mock_client_class.return_value.__aenter__.return_value = mock_client

        result = await generate_json(
            prompt="Simple prompt",
            model="gemini-2.0-flash",
            settings=settings,
        )

        assert result.data == {"result": 1}
        _, kwargs = mock_client.post.await_args
        gen_config = kwargs["json"]["generationConfig"]
        assert "responseSchema" not in gen_config
        assert "temperature" not in gen_config
