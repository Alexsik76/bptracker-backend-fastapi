from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio

from app_settings import ai_config, cache, nutrition_config
from config import get_settings
from gemini_infra.types import GeminiResult, GeminiUnavailable

MODEL_KEY = "ai.gemini.menu_model"
TEST_DATE_STR = (datetime.now(UTC).date() + timedelta(days=2)).isoformat()


@pytest.fixture(autouse=True)
def _clean_cache():
    cache.clear()
    yield
    cache.clear()


@pytest_asyncio.fixture
async def admin_client(client_factory, make_user):
    admin_id = await make_user("admin@example.com")
    return client_factory(admin_id)


def _by_key(items: list[dict]) -> dict[str, dict]:
    return {item["key"]: item for item in items}


@pytest.mark.asyncio
async def test_falls_back_to_env(admin_client):
    env = get_settings()
    assert ai_config.menu_model() == env.gemini_menu_model
    assert nutrition_config.menu_history_days() == env.menu_history_days

    resp = await admin_client.get("/admin/settings")
    assert resp.status_code == 200
    item = _by_key(resp.json())[MODEL_KEY]
    assert item["source"] == "env"
    assert item["value"] == env.gemini_menu_model
    assert item["env_value"] == env.gemini_menu_model
    assert item["updated_at"] is None
    assert item["updated_by"] is None
    assert "gemini_api_key" not in resp.text
    assert env.gemini_api_key not in resp.text


@pytest.mark.asyncio
async def test_db_value_overrides_env(admin_client):
    resp = await admin_client.put(f"/admin/settings/{MODEL_KEY}", json={"value": "gemini-x"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "db"
    assert body["value"] == "gemini-x"
    assert body["env_value"] == get_settings().gemini_menu_model
    assert body["updated_by"] is not None

    assert ai_config.menu_model() == "gemini-x"
    # Other keys keep falling back to env.
    assert ai_config.analyze_model() == get_settings().gemini_model

    listed = _by_key((await admin_client.get("/admin/settings")).json())[MODEL_KEY]
    assert listed["source"] == "db"
    assert listed["value"] == "gemini-x"


@pytest.mark.asyncio
async def test_typed_values_reach_accessors(admin_client):
    assert (
        await admin_client.put("/admin/settings/ai.gemini.timeout_seconds", json={"value": 45})
    ).status_code == 200
    assert (
        await admin_client.put("/admin/settings/ai.llm_exchange_log", json={"value": False})
    ).status_code == 200
    assert (
        await admin_client.put("/admin/settings/nutrition.menu_history_days", json={"value": 0})
    ).status_code == 200
    assert ai_config.timeout_seconds() == 45
    assert ai_config.llm_exchange_log() is False
    assert nutrition_config.menu_history_days() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("ai.gemini.timeout_seconds", 4),
        ("ai.gemini.timeout_seconds", 121),
        ("ai.gemini.timeout_seconds", "30"),
        ("ai.gemini.timeout_seconds", True),
        ("nutrition.menu_history_days", -1),
        ("nutrition.menu_history_days", 31),
        ("ai.llm_exchange_log", "yes"),
        ("ai.llm_exchange_log", 1),
        (MODEL_KEY, ""),
        (MODEL_KEY, 5),
    ],
)
async def test_validation_errors(admin_client, key, value):
    resp = await admin_client.put(f"/admin/settings/{key}", json={"value": value})
    assert resp.status_code == 422
    assert key in resp.json()["detail"]
    assert cache.get_value(key) == getattr(get_settings(), _env_attr(key))


def _env_attr(key: str) -> str:
    from app_settings.registry import REGISTRY

    return REGISTRY[key].env_attr


@pytest.mark.asyncio
async def test_unknown_key_is_404(admin_client):
    assert (await admin_client.put("/admin/settings/nope", json={"value": 1})).status_code == 404
    assert (await admin_client.delete("/admin/settings/nope")).status_code == 404


@pytest.mark.asyncio
async def test_delete_reverts_to_env(admin_client):
    await admin_client.put(f"/admin/settings/{MODEL_KEY}", json={"value": "gemini-x"})
    assert ai_config.menu_model() == "gemini-x"

    resp = await admin_client.delete(f"/admin/settings/{MODEL_KEY}")
    assert resp.status_code == 204
    assert ai_config.menu_model() == get_settings().gemini_menu_model

    listed = _by_key((await admin_client.get("/admin/settings")).json())[MODEL_KEY]
    assert listed["source"] == "env"
    # Deleting again is a no-op.
    assert (await admin_client.delete(f"/admin/settings/{MODEL_KEY}")).status_code == 204


@pytest.mark.asyncio
async def test_cache_load_reads_db_and_skips_invalid(admin_client, session):
    from app_settings.models import AppSetting

    now = datetime.now(UTC)
    session.add(AppSetting(key=MODEL_KEY, value="from-db", updated_at=now))
    session.add(AppSetting(key="ai.gemini.timeout_seconds", value=9999, updated_at=now))
    session.add(AppSetting(key="unknown.key", value=1, updated_at=now))
    await session.commit()

    await cache.load(session)
    assert ai_config.menu_model() == "from-db"
    assert ai_config.timeout_seconds() == get_settings().gemini_timeout_seconds


@pytest.mark.asyncio
async def test_generator_path_uses_new_model(admin_client):
    await admin_client.put(f"/admin/settings/{MODEL_KEY}", json={"value": "gemini-custom"})

    from nutrition.tests.test_notes import setup_test_environment

    await setup_test_environment(admin_client)
    mock_result = GeminiResult(
        data={"answer": "ok"},
        raw_text='{"answer": "ok"}',
        model="gemini-custom",
        input_tokens=1,
        output_tokens=1,
    )
    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_gen:
        mock_gen.return_value = mock_result
        resp = await admin_client.post(
            f"/nutrition/menus/{TEST_DATE_STR}/notes",
            json={"kind": "info", "text": "any question"},
        )
    assert resp.status_code == 201
    assert mock_gen.call_args.kwargs["model"] == "gemini-custom"


def _models_response(payload: dict, status_code: int = 200) -> MagicMock:
    response = MagicMock(spec=httpx.Response)
    response.status_code = status_code
    response.json.return_value = payload
    return response


@pytest.mark.asyncio
async def test_models_list_filters_generate_content(admin_client):
    page1 = _models_response(
        {
            "models": [
                {
                    "name": "models/gemini-a",
                    "displayName": "Gemini A",
                    "inputTokenLimit": 1000,
                    "outputTokenLimit": 100,
                    "supportedGenerationMethods": ["generateContent", "countTokens"],
                },
                {
                    "name": "models/embed-b",
                    "displayName": "Embed B",
                    "supportedGenerationMethods": ["embedContent"],
                },
            ],
            "nextPageToken": "tok",
        }
    )
    page2 = _models_response(
        {
            "models": [
                {
                    "name": "models/gemini-c",
                    "displayName": "Gemini C",
                    "inputTokenLimit": 2000,
                    "outputTokenLimit": 200,
                    "supportedGenerationMethods": ["generateContent"],
                }
            ]
        }
    )
    mock_client = AsyncMock()
    mock_client.get.side_effect = [page1, page2]
    with patch("app_settings.ai_service.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__.return_value = mock_client
        resp = await admin_client.get("/admin/settings/ai/models")

    assert resp.status_code == 200
    assert resp.json() == [
        {
            "name": "gemini-a",
            "display_name": "Gemini A",
            "input_token_limit": 1000,
            "output_token_limit": 100,
        },
        {
            "name": "gemini-c",
            "display_name": "Gemini C",
            "input_token_limit": 2000,
            "output_token_limit": 200,
        },
    ]
    first_call = mock_client.get.call_args_list[0]
    assert first_call.kwargs["headers"] == {"x-goog-api-key": get_settings().gemini_api_key}
    assert mock_client.get.call_args_list[1].kwargs["params"]["pageToken"] == "tok"


@pytest.mark.asyncio
async def test_models_list_502_when_unreachable(admin_client):
    mock_client = AsyncMock()
    mock_client.get.side_effect = httpx.ConnectError("boom")
    with patch("app_settings.ai_service.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__.return_value = mock_client
        resp = await admin_client.get("/admin/settings/ai/models")
    assert resp.status_code == 502
    assert resp.json()["detail"]
    assert get_settings().gemini_api_key not in resp.text


@pytest.mark.asyncio
async def test_models_list_502_on_upstream_error_status(admin_client):
    mock_client = AsyncMock()
    mock_client.get.return_value = _models_response({}, status_code=403)
    with patch("app_settings.ai_service.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__.return_value = mock_client
        resp = await admin_client.get("/admin/settings/ai/models")
    assert resp.status_code == 502


@pytest.mark.asyncio
async def test_ai_test_success_and_default_model(admin_client):
    result = GeminiResult(
        data={"ok": True}, raw_text="{}", model="m", input_tokens=1, output_tokens=1
    )
    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_gen:
        mock_gen.return_value = result
        resp = await admin_client.post("/admin/settings/ai/test", json={})
        explicit = await admin_client.post("/admin/settings/ai/test", json={"model": "gemini-z"})

    body = resp.json()
    assert resp.status_code == 200
    assert body["ok"] is True
    assert body["error"] is None
    assert body["model"] == get_settings().gemini_model
    assert body["latency_ms"] >= 0
    assert explicit.json()["model"] == "gemini-z"
    assert mock_gen.call_args.kwargs["model"] == "gemini-z"


@pytest.mark.asyncio
async def test_ai_test_reports_model_error_without_5xx(admin_client):
    with patch("gemini_infra.generate_json", new_callable=AsyncMock) as mock_gen:
        mock_gen.side_effect = GeminiUnavailable()
        resp = await admin_client.post("/admin/settings/ai/test", json={"model": "bad-model"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["model"] == "bad-model"
    assert body["error"]


@pytest.mark.asyncio
async def test_non_admin_forbidden(client_factory, make_user):
    user_id = await make_user("testuser@example.com")
    client = client_factory(user_id)
    assert (await client.get("/admin/settings")).status_code == 403
    assert (
        await client.put(f"/admin/settings/{MODEL_KEY}", json={"value": "x"})
    ).status_code == 403
    assert (await client.delete(f"/admin/settings/{MODEL_KEY}")).status_code == 403
    assert (await client.get("/admin/settings/ai/models")).status_code == 403
    assert (await client.post("/admin/settings/ai/test", json={})).status_code == 403
