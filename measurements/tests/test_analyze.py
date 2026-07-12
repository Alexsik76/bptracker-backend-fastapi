from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlmodel import select

from config import get_settings
from main import app
from measurements.models import Measurement


@pytest.mark.asyncio
async def test_analyze_happy_path(client, session):
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "candidates": [
            {"content": {"parts": [{"text": '{"systolic": 120, "diastolic": 80, "pulse": 65}'}]}}
        ]
    }

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response

    with patch("measurements.analyze.httpx.AsyncClient") as mock_client_class:
        mock_client_class.return_value.__aenter__.return_value = mock_client

        resp = await client.post(
            "/measurements/analyze",
            files={"image": ("test.jpg", b"fakeimagebytes", "image/jpeg")},
        )

        assert resp.status_code == 200
        assert resp.json() == {"sys": 120, "dia": 80, "pulse": 65}

    # Verify no measurement was created in the database
    measurements = (await session.exec(select(Measurement))).all()
    assert len(measurements) == 0


@pytest.mark.asyncio
async def test_analyze_code_fence_wrapped(client, session):
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": (
                                "```json\n"
                                "{\n"
                                '  "systolic": 130,\n'
                                '  "diastolic": 85,\n'
                                '  "pulse": 70\n'
                                "}\n"
                                "```"
                            )
                        }
                    ]
                }
            }
        ]
    }

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response

    with patch("measurements.analyze.httpx.AsyncClient") as mock_client_class:
        mock_client_class.return_value.__aenter__.return_value = mock_client

        resp = await client.post(
            "/measurements/analyze",
            files={"image": ("test.jpg", b"fakeimagebytes", "image/jpeg")},
        )

        assert resp.status_code == 200
        assert resp.json() == {"sys": 130, "dia": 85, "pulse": 70}

    # Verify database remains empty
    measurements = (await session.exec(select(Measurement))).all()
    assert len(measurements) == 0


@pytest.mark.asyncio
async def test_analyze_values_out_of_range(client, session):
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "candidates": [
            {"content": {"parts": [{"text": '{"systolic": 900, "diastolic": 80, "pulse": 65}'}]}}
        ]
    }

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response

    with patch("measurements.analyze.httpx.AsyncClient") as mock_client_class:
        mock_client_class.return_value.__aenter__.return_value = mock_client

        resp = await client.post(
            "/measurements/analyze",
            files={"image": ("test.jpg", b"fakeimagebytes", "image/jpeg")},
        )

        assert resp.status_code == 422
        assert resp.json()["detail"] == "Could not recognise values from the image"

    # Verify database remains empty
    measurements = (await session.exec(select(Measurement))).all()
    assert len(measurements) == 0


@pytest.mark.asyncio
async def test_analyze_gemini_timeout_or_error(client, session):
    # Timeout case
    mock_client_timeout = AsyncMock()
    mock_client_timeout.post.side_effect = httpx.ConnectTimeout("Connection timed out")

    with patch("measurements.analyze.httpx.AsyncClient") as mock_client_class:
        mock_client_class.return_value.__aenter__.return_value = mock_client_timeout

        resp = await client.post(
            "/measurements/analyze",
            files={"image": ("test.jpg", b"fakeimagebytes", "image/jpeg")},
        )
        assert resp.status_code == 502
        assert resp.json()["detail"] == "Recognition service unavailable"

    # HTTP 500 case
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 500
    mock_response.text = "Internal Server Error"

    mock_client_err = AsyncMock()
    mock_client_err.post.return_value = mock_response

    with patch("measurements.analyze.httpx.AsyncClient") as mock_client_class:
        mock_client_class.return_value.__aenter__.return_value = mock_client_err

        resp = await client.post(
            "/measurements/analyze",
            files={"image": ("test.jpg", b"fakeimagebytes", "image/jpeg")},
        )
        assert resp.status_code == 502
        assert resp.json()["detail"] == "Recognition service unavailable"

    # Verify database remains empty
    measurements = (await session.exec(select(Measurement))).all()
    assert len(measurements) == 0


@pytest.mark.asyncio
async def test_analyze_gemini_unparseable_text(client, session):
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": "invalid json output"}]}}]
    }

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response

    with patch("measurements.analyze.httpx.AsyncClient") as mock_client_class:
        mock_client_class.return_value.__aenter__.return_value = mock_client

        resp = await client.post(
            "/measurements/analyze",
            files={"image": ("test.jpg", b"fakeimagebytes", "image/jpeg")},
        )
        assert resp.status_code == 422
        assert resp.json()["detail"] == "Could not recognise values from the image"

    # Verify database remains empty
    measurements = (await session.exec(select(Measurement))).all()
    assert len(measurements) == 0


@pytest.mark.asyncio
async def test_analyze_input_validations(client, session):
    # 1. Missing file
    resp = await client.post("/measurements/analyze")
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Image file is missing"

    # 2. Non-image content type
    resp = await client.post(
        "/measurements/analyze",
        files={"image": ("test.txt", b"hello content", "text/plain")},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "File must be an image"

    # 3. Oversized file (patching limit to 5 bytes)
    settings = get_settings()
    original_limit = settings.analyze_max_file_bytes
    settings.analyze_max_file_bytes = 5
    try:
        resp = await client.post(
            "/measurements/analyze",
            files={"image": ("test.jpg", b"1234567890", "image/jpeg")},
        )
        assert resp.status_code == 413
        assert resp.json()["detail"] == "File exceeds 10 MB"
    finally:
        settings.analyze_max_file_bytes = original_limit


@pytest.mark.asyncio
async def test_analyze_no_auth(session):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon_client:
        resp = await anon_client.post(
            "/measurements/analyze",
            files={"image": ("test.jpg", b"fakeimagebytes", "image/jpeg")},
        )
        assert resp.status_code == 401
