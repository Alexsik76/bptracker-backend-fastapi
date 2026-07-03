from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from auth.models import MagicLink
from auth.security import hash_magic_token
from email_infra import EmailSender, get_email_sender
from main import app


@pytest.fixture
def mock_email_sender():
    mock = AsyncMock(spec=EmailSender)
    app.dependency_overrides[get_email_sender] = lambda: mock
    yield mock
    # Clear the specific override after test
    app.dependency_overrides.pop(get_email_sender, None)


@pytest.mark.asyncio
async def test_request_magic_link_known_email(
    client, make_user, session: AsyncSession, mock_email_sender
):
    # Arrange
    email = "known@example.com"
    await make_user(email)

    # Act
    response = await client.post("/auth/magic-link/request", json={"email": email})

    # Assert
    assert response.status_code == 202
    assert response.json()["detail"] == "If the address is registered, a link has been sent"

    # Verify email was sent
    mock_email_sender.send.assert_called_once()
    _, kwargs = mock_email_sender.send.call_args
    assert kwargs["to"] == email
    assert "token=" in kwargs["text"]
    assert "token=" in kwargs["html"]

    # Extract raw token from link in email text
    # text format is f"Use the link below to log in to your account:\n{magic_link}"
    text_content = kwargs["text"]
    url_line = [line for line in text_content.split("\n") if "token=" in line][0]
    raw_token = url_line.split("token=")[1].strip()

    # Verify database state
    statement = select(MagicLink).where(MagicLink.email == email)
    result = await session.exec(statement)
    link = result.first()

    assert link is not None
    # Raw token is not stored, only its hash
    assert link.token_hash == hash_magic_token(raw_token)
    assert raw_token != link.token_hash


@pytest.mark.asyncio
async def test_request_magic_link_unknown_email(client, session: AsyncSession, mock_email_sender):
    # Act
    response = await client.post("/auth/magic-link/request", json={"email": "unknown@example.com"})

    # Assert
    assert response.status_code == 202
    assert response.json()["detail"] == "If the address is registered, a link has been sent"

    # Verify no email was sent and no DB row created
    mock_email_sender.send.assert_not_called()

    statement = select(MagicLink).where(MagicLink.email == "unknown@example.com")
    result = await session.exec(statement)
    assert result.first() is None


@pytest.mark.asyncio
async def test_request_magic_link_replaces_old_link(
    client, make_user, session: AsyncSession, mock_email_sender
):
    # Arrange
    email = "known@example.com"
    await make_user(email)

    # Act - First request
    response1 = await client.post("/auth/magic-link/request", json={"email": email})
    assert response1.status_code == 202

    # Get first hash
    statement = select(MagicLink).where(MagicLink.email == email)
    link1 = (await session.exec(statement)).first()
    assert link1 is not None
    hash1 = link1.token_hash

    # Act - Second request
    response2 = await client.post("/auth/magic-link/request", json={"email": email})
    assert response2.status_code == 202

    # Assert only one row remains, but with a different hash
    # Expire session so we fetch fresh data
    session.expire_all()
    result = await session.exec(statement)
    links = result.all()

    assert len(links) == 1
    assert links[0].token_hash != hash1


@pytest.mark.asyncio
async def test_confirm_magic_link_success(
    client, make_user, session: AsyncSession, mock_email_sender
):
    # Arrange
    email = "alice@example.com"
    await make_user(email)

    # Generate link
    await client.post("/auth/magic-link/request", json={"email": email})
    mock_email_sender.send.assert_called_once()
    text_content = mock_email_sender.send.call_args[1]["text"]
    url_line = [line for line in text_content.split("\n") if "token=" in line][0]
    raw_token = url_line.split("token=")[1].strip()

    # Act - Confirm
    response = await client.post("/auth/magic-link/confirm", json={"token": raw_token})

    # Assert
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]

    # Verify link is deleted
    session.expire_all()
    statement = select(MagicLink).where(MagicLink.email == email)
    link = (await session.exec(statement)).first()
    assert link is None


@pytest.mark.asyncio
async def test_confirm_magic_link_expired(client, make_user, session: AsyncSession):
    # Arrange
    email = "expired@example.com"
    await make_user(email)

    # Manually insert an expired link
    raw_token = "some-expired-token"
    token_hash = hash_magic_token(raw_token)
    expired_at = datetime.now(UTC) - timedelta(minutes=1)
    db_link = MagicLink(email=email, token_hash=token_hash, expires_at=expired_at)
    session.add(db_link)
    await session.commit()

    # Act - Confirm
    response = await client.post("/auth/magic-link/confirm", json={"token": raw_token})

    # Assert
    assert response.status_code == 401
    assert response.json()["detail"] == "Incorrect or expired token"

    # Verify expired link is cleaned up/deleted
    session.expire_all()
    statement = select(MagicLink).where(MagicLink.email == email)
    link = (await session.exec(statement)).first()
    assert link is None


@pytest.mark.asyncio
async def test_confirm_magic_link_reused(
    client, make_user, session: AsyncSession, mock_email_sender
):
    # Arrange
    email = "reused@example.com"
    await make_user(email)

    # Request link
    await client.post("/auth/magic-link/request", json={"email": email})
    text_content = mock_email_sender.send.call_args[1]["text"]
    url_line = [line for line in text_content.split("\n") if "token=" in line][0]
    raw_token = url_line.split("token=")[1].strip()

    # Confirm 1st time - Success
    res1 = await client.post("/auth/magic-link/confirm", json={"token": raw_token})
    assert res1.status_code == 200

    # Confirm 2nd time - Fails
    res2 = await client.post("/auth/magic-link/confirm", json={"token": raw_token})
    assert res2.status_code == 401
    assert res2.json()["detail"] == "Incorrect or expired token"


@pytest.mark.asyncio
async def test_confirm_magic_link_invalid_token(client):
    # Act
    response = await client.post("/auth/magic-link/confirm", json={"token": "garbage-token"})

    # Assert
    assert response.status_code == 401
    assert response.json()["detail"] == "Incorrect or expired token"
