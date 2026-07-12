import pytest
from httpx import ASGITransport, AsyncClient

from main import app


@pytest.mark.asyncio
async def test_get_me_returns_caller_profile(client_factory, make_user):
    user_id = await make_user("profile_test@example.com")

    client = client_factory(user_id)
    response = await client.get("/users/me")

    assert response.status_code == 200
    data = response.json()

    assert data["id"] == str(user_id)
    assert data["email"] == "profile_test@example.com"
    assert "password_hash" not in data
    assert "last_export_at" not in data


@pytest.mark.asyncio
async def test_get_me_unauthorized():
    # Clear overrides to trigger actual authentication flow
    app.dependency_overrides.clear()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.get("/users/me")
    assert response.status_code == 401
