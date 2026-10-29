import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from main import app


@pytest.mark.asyncio
async def test_admin_ping_unauthorized():
    app.dependency_overrides.clear()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/admin/ping")
    assert response.status_code == 401
    assert response.json()["detail"] == "Not authenticated"


@pytest.mark.asyncio
async def test_admin_ping_forbidden_for_non_admin(client_factory, make_user):
    user_id = await make_user("testuser@example.com")
    client = client_factory(user_id)
    response = await client.get("/admin/ping")
    assert response.status_code == 403
    assert response.json()["detail"] == "Admin only"


@pytest.mark.asyncio
async def test_admin_ping_success_for_admin(client_factory, make_user):
    admin_id = await make_user("admin@example.com")
    client = client_factory(admin_id)
    response = await client.get("/admin/ping")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_admin_ping_nonexistent_user(client_factory):
    client = client_factory(uuid.uuid4())
    response = await client.get("/admin/ping")
    assert response.status_code == 401
    assert response.json()["detail"] == "User not found"
