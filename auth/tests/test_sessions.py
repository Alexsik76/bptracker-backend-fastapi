from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from auth.models import Session
from auth.security import hash_token
from auth.webauthn.tests.test_ceremonies import (
    SoftWebauthnDevice,
    make_authentication_verify_body,
    make_registration_verify_body,
    prepare_options_for_soft_webauthn,
)


@pytest.fixture
def register_payload():
    return {"email": "test_session@example.com", "password": "supersecret123"}


@pytest.mark.asyncio
async def test_password_login_returns_token_pair(client, register_payload):
    # Register
    reg_resp = await client.post("/auth/register", json=register_payload)
    assert reg_resp.status_code == 201
    reg_body = reg_resp.json()
    assert "access_token" in reg_body
    assert "refresh_token" in reg_body
    assert reg_body["token_type"] == "bearer"
    assert reg_body["expires_in"] == 900

    # Login
    login_resp = await client.post("/auth/login", json=register_payload)
    assert login_resp.status_code == 200
    login_body = login_resp.json()
    assert "access_token" in login_body
    assert "refresh_token" in login_body
    assert login_body["token_type"] == "bearer"
    assert login_body["expires_in"] == 900


@pytest.mark.asyncio
async def test_magic_link_confirm_returns_token_pair(
    client, make_user, session: AsyncSession, register_payload
):
    email = register_payload["email"]
    await make_user(email)

    from unittest.mock import AsyncMock

    from email_infra import EmailSender, get_email_sender
    from main import app

    mock_email_sender = AsyncMock(spec=EmailSender)
    app.dependency_overrides[get_email_sender] = lambda: mock_email_sender

    try:
        # Request magic link
        await client.post("/auth/magic-link/request", json={"email": email})
        assert mock_email_sender.send.called

        # Extract token
        text_content = mock_email_sender.send.call_args[1]["text"]
        url_line = [line for line in text_content.split("\n") if "token=" in line][0]
        raw_token = url_line.split("token=")[1].strip()

        # Confirm magic link
        confirm_resp = await client.post("/auth/magic-link/confirm", json={"token": raw_token})
        assert confirm_resp.status_code == 200
        body = confirm_resp.json()
        assert "access_token" in body
        assert "refresh_token" in body
        assert body["token_type"] == "bearer"
        assert body["expires_in"] == 900
    finally:
        app.dependency_overrides.pop(get_email_sender, None)


@pytest.mark.asyncio
async def test_webauthn_authenticate_returns_token_pair(
    client_factory, make_user, session: AsyncSession
):
    user_id = await make_user("webauthn_session@example.com")
    client = client_factory(user_id)
    origin = "http://localhost:5173"

    # Register device
    reg_options_resp = await client.post("/auth/webauthn/register/options")
    options_dict = reg_options_resp.json()
    device = SoftWebauthnDevice()
    device_response = device.create(prepare_options_for_soft_webauthn(options_dict), origin=origin)
    await client.post(
        "/auth/webauthn/register/verify", json=make_registration_verify_body(device_response)
    )

    # Start authentication
    auth_options_resp = await client.post("/auth/webauthn/authenticate/options")
    auth_options_dict = auth_options_resp.json()

    # Emulate device get
    auth_device_response = device.get(
        prepare_options_for_soft_webauthn(auth_options_dict), origin=origin
    )
    auth_verify_body = make_authentication_verify_body(auth_device_response)

    # Verify authentication
    auth_verify_resp = await client.post(
        "/auth/webauthn/authenticate/verify", json=auth_verify_body
    )
    assert auth_verify_resp.status_code == 200
    body = auth_verify_resp.json()
    assert "access_token" in body
    assert "refresh_token" in body
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 900


@pytest.mark.asyncio
async def test_refresh_token_rotation_and_revocation(
    client, register_payload, session: AsyncSession
):
    # Register
    await client.post("/auth/register", json=register_payload)

    # Login to get fresh tokens
    login_resp = await client.post("/auth/login", json=register_payload)
    assert login_resp.status_code == 200
    login_body = login_resp.json()
    raw_refresh = login_body["refresh_token"]

    # Refresh - should rotate token
    refresh_resp = await client.post("/auth/refresh", json={"refresh_token": raw_refresh})
    assert refresh_resp.status_code == 200
    refresh_body = refresh_resp.json()
    new_refresh = refresh_body["refresh_token"]
    assert new_refresh != raw_refresh

    # Using old token again should fail
    old_refresh_resp = await client.post("/auth/refresh", json={"refresh_token": raw_refresh})
    assert old_refresh_resp.status_code == 401
    assert old_refresh_resp.json()["detail"] == "Invalid or expired refresh token"


@pytest.mark.asyncio
async def test_refresh_token_reuse_detection(client, register_payload, session: AsyncSession):
    # Register & Login
    await client.post("/auth/register", json=register_payload)
    login_resp = await client.post("/auth/login", json=register_payload)
    raw_refresh = login_resp.json()["refresh_token"]

    # First rotation (succeeds)
    refresh_resp1 = await client.post("/auth/refresh", json={"refresh_token": raw_refresh})
    assert refresh_resp1.status_code == 200
    new_refresh1 = refresh_resp1.json()["refresh_token"]

    # Second login to create a parallel family
    login_resp2 = await client.post("/auth/login", json=register_payload)
    raw_refresh2 = login_resp2.json()["refresh_token"]

    # Reuse detection triggered by using raw_refresh again (which was already rotated)
    import asyncio

    await asyncio.sleep(0.6)
    reuse_resp = await client.post("/auth/refresh", json={"refresh_token": raw_refresh})
    assert reuse_resp.status_code == 401
    assert reuse_resp.json()["detail"] == "Invalid or expired refresh token"

    # All sessions of that user must now be revoked
    session.expire_all()
    stmt = select(Session)
    res = await session.exec(stmt)
    all_sessions = res.all()
    assert len(all_sessions) > 0
    for s in all_sessions:
        assert s.revoked_at is not None

    # The new refresh token from family 1 should now fail
    failed_refresh1 = await client.post("/auth/refresh", json={"refresh_token": new_refresh1})
    assert failed_refresh1.status_code == 401

    # The refresh token from family 2 should also fail
    failed_refresh2 = await client.post("/auth/refresh", json={"refresh_token": raw_refresh2})
    assert failed_refresh2.status_code == 401


@pytest.mark.asyncio
async def test_expired_refresh_token(client, register_payload, session: AsyncSession):
    # Register & Login
    await client.post("/auth/register", json=register_payload)
    login_resp = await client.post("/auth/login", json=register_payload)
    raw_refresh = login_resp.json()["refresh_token"]

    # Artificially expire the session in the DB
    session.expire_all()
    token_hash = hash_token(raw_refresh)
    stmt = select(Session).where(Session.token_hash == token_hash)
    res = await session.exec(stmt)
    db_session = res.one()
    db_session.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    session.add(db_session)
    await session.commit()

    # Refresh should fail with 401
    refresh_resp = await client.post("/auth/refresh", json={"refresh_token": raw_refresh})
    assert refresh_resp.status_code == 401
    assert refresh_resp.json()["detail"] == "Invalid or expired refresh token"


@pytest.mark.asyncio
async def test_logout_invalidates_session(client, register_payload, session: AsyncSession):
    # Register & Login
    await client.post("/auth/register", json=register_payload)
    login_resp = await client.post("/auth/login", json=register_payload)
    raw_refresh = login_resp.json()["refresh_token"]

    # Logout
    logout_resp = await client.post("/auth/logout", json={"refresh_token": raw_refresh})
    assert logout_resp.status_code == 204

    # Using the logged out token to refresh should fail
    refresh_resp = await client.post("/auth/refresh", json={"refresh_token": raw_refresh})
    assert refresh_resp.status_code == 401

    # Double logout still returns 204
    logout_resp2 = await client.post("/auth/logout", json={"refresh_token": raw_refresh})
    assert logout_resp2.status_code == 204


@pytest.mark.asyncio
async def test_logout_all(client_factory, make_user, session: AsyncSession):
    user_a = await make_user("user_a@example.com")
    user_b = await make_user("user_b@example.com")

    client_a = client_factory(user_a)
    client_b = client_factory(user_b)

    # Issue 2 sessions for User A
    token_a1 = await client_a.post(
        "/auth/login", json={"email": "user_a@example.com", "password": "test-password-123"}
    )
    token_a2 = await client_a.post(
        "/auth/login", json={"email": "user_a@example.com", "password": "test-password-123"}
    )
    assert token_a1.status_code == 200
    assert token_a2.status_code == 200

    # Issue 1 session for User B
    token_b = await client_b.post(
        "/auth/login", json={"email": "user_b@example.com", "password": "test-password-123"}
    )
    assert token_b.status_code == 200

    # Call logout-all as User A
    from auth.deps import get_current_user_id
    from main import app

    app.dependency_overrides[get_current_user_id] = lambda: user_a
    logout_resp = await client_a.post("/auth/logout-all")
    assert logout_resp.status_code == 204

    # Verify both of User A's sessions are revoked, and User B's is still active
    session.expire_all()
    stmt_a = select(Session).where(Session.user_id == user_a)
    res_a = await session.exec(stmt_a)
    for s in res_a.all():
        assert s.revoked_at is not None

    stmt_b = select(Session).where(Session.user_id == user_b)
    res_b = await session.exec(stmt_b)
    for s in res_b.all():
        assert s.revoked_at is None


@pytest.mark.asyncio
async def test_get_sessions(client_factory, make_user, session: AsyncSession):
    user_id = await make_user("sessions_list@example.com")
    client = client_factory(user_id)

    # Issue two sessions
    await client.post(
        "/auth/login",
        json={"email": "sessions_list@example.com", "password": "test-password-123"},
        headers={"User-Agent": "Browser-Agent-A"},
    )
    await client.post(
        "/auth/login",
        json={"email": "sessions_list@example.com", "password": "test-password-123"},
        headers={"User-Agent": "Browser-Agent-B"},
    )

    # Get sessions list
    list_resp = await client.get("/auth/sessions")
    assert list_resp.status_code == 200
    data = list_resp.json()
    assert len(data) == 2

    # Check mapping
    agents = [item["user_agent"] for item in data]
    assert "Browser-Agent-A" in agents
    assert "Browser-Agent-B" in agents

    # Ensure sensitive fields are not exposed
    for item in data:
        assert "token_hash" not in item
        assert "user_id" not in item
        assert "id" in item
        assert "created_at" in item
        assert "expires_at" in item


@pytest.mark.asyncio
async def test_concurrent_rotate_session(make_user, session: AsyncSession):
    import asyncio

    from auth.service import (
        InvalidOrExpiredTokenError,
        TokenPair,
        issue_session,
        list_active_sessions,
        rotate_session,
    )
    from conftest import test_session_factory

    user_id = await make_user("concurrent_rot@example.com")

    # Issue a session first using a clean DB session
    async with test_session_factory() as db_sess:
        token_pair = await issue_session(db_sess, user_id=user_id, user_agent="Concurrent-UA")
        raw_refresh = token_pair.refresh_token

    # Define the concurrent rotation tasks using separate DB sessions
    async def run_rotation():
        async with test_session_factory() as db_sess:
            return await rotate_session(
                db_sess, raw_token=raw_refresh, user_agent="Concurrent-UA-Rotated"
            )

    # Run two rotate_session calls concurrently
    results = await asyncio.gather(run_rotation(), run_rotation(), return_exceptions=True)

    # Exactly one must succeed, and the other must raise InvalidOrExpiredTokenError
    successes = [r for r in results if isinstance(r, TokenPair)]
    failures = [r for r in results if isinstance(r, InvalidOrExpiredTokenError)]

    assert len(successes) == 1
    assert len(failures) == 1

    # The user ends up with exactly one active session
    async with test_session_factory() as db_sess:
        active_sessions = await list_active_sessions(db_sess, user_id=user_id)
        assert len(active_sessions) == 1
