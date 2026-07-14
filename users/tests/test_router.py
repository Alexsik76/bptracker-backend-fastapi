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
    assert "last_export_at" not in data


@pytest.mark.asyncio
async def test_get_me_unauthorized():
    # Clear overrides to trigger actual authentication flow
    app.dependency_overrides.clear()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.get("/users/me")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_delete_me_requires_authentication():
    app.dependency_overrides.clear()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.delete("/users/me")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_delete_me_success(client_factory, make_user, session):
    from datetime import UTC, datetime, timedelta

    from sqlmodel import select

    from auth.models import MagicLink
    from auth.models import Session as DB_Session
    from auth.security import hash_token
    from measurements.models import Measurement

    email = "allowed@example.com"
    user_id = await make_user(email)

    # 1. Create a measurement for this user
    measurement = Measurement(
        user_id=user_id,
        sys=120,
        dia=80,
        pulse=70,
    )
    session.add(measurement)

    # 2. Create a session for this user
    db_session = DB_Session(
        user_id=user_id,
        token_hash=hash_token("test-session-token"),
        expires_at=datetime.now(UTC) + timedelta(days=1),
        user_agent="Test-Agent",
    )
    session.add(db_session)

    # 3. Create a magic link for this email
    magic_link = MagicLink(
        email=email,
        token_hash=hash_token("test-magic-token"),
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    session.add(magic_link)

    await session.commit()

    # Verify child rows exist before deletion
    has_m = (
        await session.exec(select(Measurement).where(Measurement.user_id == user_id))
    ).first() is not None
    assert has_m

    has_s = (
        await session.exec(select(DB_Session).where(DB_Session.user_id == user_id))
    ).first() is not None
    assert has_s

    has_l = (
        await session.exec(select(MagicLink).where(MagicLink.email == email))
    ).first() is not None
    assert has_l

    # Act: call DELETE /users/me
    client = client_factory(user_id)
    delete_response = await client.delete("/users/me")
    assert delete_response.status_code == 204

    # Assert GET /users/me returns 404
    get_response = await client.get("/users/me")
    assert get_response.status_code == 404

    # Assert child rows and magic links are gone
    session.expire_all()
    has_m_after = (
        await session.exec(select(Measurement).where(Measurement.user_id == user_id))
    ).first()
    assert has_m_after is None

    has_s_after = (
        await session.exec(select(DB_Session).where(DB_Session.user_id == user_id))
    ).first()
    assert has_s_after is None

    has_l_after = (await session.exec(select(MagicLink).where(MagicLink.email == email))).first()
    assert has_l_after is None
