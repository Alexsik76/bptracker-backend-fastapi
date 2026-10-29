import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from activity.models import ErrorEvent, RequestEvent
from activity.writer import clear_activity_queue, flush_activity_queue
from auth.security import create_access_token
from config import get_settings
from db import get_session
from main import app


@app.get("/test-activity-trigger-crash", include_in_schema=False)
def _trigger_crash():
    raise ValueError("simulated unhandled crash")


@app.get("/test-activity-trigger-http500", include_in_schema=False)
def _trigger_http500():
    from fastapi import HTTPException

    raise HTTPException(status_code=500, detail="intentional 500")


@pytest.mark.asyncio
async def test_middleware_records_route_template_client_user_id_and_status(
    session: AsyncSession, make_user
):
    clear_activity_queue()
    user_id = await make_user("alice@example.com")
    token = create_access_token(user_id)
    measurement_id = uuid.uuid4()

    app.dependency_overrides[get_session] = lambda: session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        response = await c.get(
            f"/measurements/{measurement_id}",
            headers={"Authorization": f"Bearer {token}", "X-Client": "ios"},
        )
    # The route exists (/measurements/{measurement_id}), returns 404 since measurement doesn't exist
    assert response.status_code == 404

    flushed = await flush_activity_queue(session)
    assert flushed == 1

    events = (await session.exec(select(RequestEvent))).all()
    assert len(events) == 1
    event = events[0]
    assert event.route == "/measurements/{measurement_id}"
    assert event.client == "ios"
    assert event.user_id == user_id
    assert event.module == "measurements"
    assert event.method == "GET"
    assert event.status_code == 404
    assert event.duration_ms >= 0


@pytest.mark.asyncio
async def test_middleware_client_android_from_okhttp(session: AsyncSession):
    clear_activity_queue()
    app.dependency_overrides[get_session] = lambda: session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await c.get("/health", headers={"User-Agent": "okhttp/4.9.0"})
        # /health is skipped, so nothing queued
        assert await flush_activity_queue(session) == 0

        # Now non-health endpoint
        await c.get("/admin/ping", headers={"User-Agent": "okhttp/4.9.0"})

    assert await flush_activity_queue(session) == 1
    events = (await session.exec(select(RequestEvent))).all()
    assert len(events) == 1
    assert events[0].client == "android"


@pytest.mark.asyncio
async def test_middleware_client_other_default(session: AsyncSession):
    clear_activity_queue()
    app.dependency_overrides[get_session] = lambda: session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await c.get("/admin/ping", headers={"User-Agent": "Mozilla/5.0"})

    assert await flush_activity_queue(session) == 1
    events = (await session.exec(select(RequestEvent))).all()
    assert len(events) == 1
    assert events[0].client == "other"


@pytest.mark.asyncio
async def test_middleware_failing_token_results_in_null_user(session: AsyncSession):
    clear_activity_queue()
    app.dependency_overrides[get_session] = lambda: session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        response = await c.get(
            "/admin/ping",
            headers={"Authorization": "Bearer not-a-valid-jwt-token"},
        )
    assert response.status_code == 401

    assert await flush_activity_queue(session) == 1
    events = (await session.exec(select(RequestEvent))).all()
    assert len(events) == 1
    assert events[0].user_id is None
    assert events[0].status_code == 401


@pytest.mark.asyncio
async def test_middleware_skips_health_and_options(session: AsyncSession):
    clear_activity_queue()
    app.dependency_overrides[get_session] = lambda: session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        res_health = await c.get("/health")
        assert res_health.status_code == 200

        res_opt = await c.options("/measurements")
        # Preflight or options
        assert res_opt.status_code in (200, 405)

    assert await flush_activity_queue(session) == 0
    events = (await session.exec(select(RequestEvent))).all()
    assert len(events) == 0


@pytest.mark.asyncio
async def test_middleware_exception_records_error_event_and_propagates(session: AsyncSession):
    clear_activity_queue()
    app.dependency_overrides[get_session] = lambda: session
    transport = ASGITransport(app=app, raise_app_exceptions=True)

    async with AsyncClient(transport=transport, base_url="http://test") as c:
        with pytest.raises(ValueError, match="simulated unhandled crash"):
            await c.get("/test-activity-trigger-crash")

    # Both ErrorEvent and RequestEvent (status 500) should be queued
    assert await flush_activity_queue(session) == 2

    errors = (await session.exec(select(ErrorEvent))).all()
    assert len(errors) == 1
    err = errors[0]
    assert err.exception_type == "ValueError"
    assert "simulated unhandled crash" in err.message
    assert err.route == "/test-activity-trigger-crash"
    assert "ValueError: simulated unhandled crash" in err.traceback
    assert len(err.traceback) <= 20000

    requests = (await session.exec(select(RequestEvent))).all()
    assert len(requests) == 1
    req = requests[0]
    assert req.status_code == 500
    assert req.route == "/test-activity-trigger-crash"


@pytest.mark.asyncio
async def test_middleware_5xx_without_exception_records_request_event_only(session: AsyncSession):
    clear_activity_queue()
    app.dependency_overrides[get_session] = lambda: session
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as c:
        response = await c.get("/test-activity-trigger-http500")
        assert response.status_code == 500

    assert await flush_activity_queue(session) == 1

    errors = (await session.exec(select(ErrorEvent))).all()
    assert len(errors) == 0

    requests = (await session.exec(select(RequestEvent))).all()
    assert len(requests) == 1
    assert requests[0].status_code == 500


@pytest.mark.asyncio
async def test_middleware_disabled_setting(session: AsyncSession, monkeypatch):
    clear_activity_queue()
    settings = get_settings()
    monkeypatch.setattr(settings, "activity_log_enabled", False)

    app.dependency_overrides[get_session] = lambda: session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await c.get("/admin/ping")

    assert await flush_activity_queue(session) == 0
    events = (await session.exec(select(RequestEvent))).all()
    assert len(events) == 0
