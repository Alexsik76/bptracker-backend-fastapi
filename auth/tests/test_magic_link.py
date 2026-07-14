from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from auth.models import MagicLink, User
from auth.security import hash_token
from email_infra.models import EmailOutbox


@pytest.mark.asyncio
async def test_request_magic_link_allowlisted_unknown_email(client, session: AsyncSession):
    # Arrange
    email = "new-user@example.com"  # Present in ALLOWED_EMAILS env in conftest.py

    # Act
    response = await client.post("/auth/magic-link/request", json={"email": email})

    # Assert
    assert response.status_code == 202
    assert response.json()["detail"] == "If the address is registered, a link has been sent"

    # Verify user was created
    statement = select(User).where(User.email == email)
    user = (await session.exec(statement)).first()
    assert user is not None
    assert user.email == email

    # Verify email was enqueued in outbox
    statement_outbox = select(EmailOutbox).where(EmailOutbox.to == email)
    outbox_item = (await session.exec(statement_outbox)).first()
    assert outbox_item is not None
    assert outbox_item.status == "PENDING"
    assert "token=" in outbox_item.body

    # Verify magic link exists in DB
    statement_link = select(MagicLink).where(MagicLink.email == email)
    link = (await session.exec(statement_link)).first()
    assert link is not None


@pytest.mark.asyncio
async def test_request_magic_link_allowlisted_known_email(client, make_user, session: AsyncSession):
    # Arrange
    email = "existing@example.com"  # Present in ALLOWED_EMAILS env
    user_id = await make_user(email)

    # Act
    response = await client.post("/auth/magic-link/request", json={"email": email})

    # Assert
    assert response.status_code == 202

    # Verify no duplicate user was created
    statement = select(User).where(User.email == email)
    users = (await session.exec(statement)).all()
    assert len(users) == 1
    assert users[0].id == user_id

    # Verify email was enqueued in outbox
    statement_outbox = select(EmailOutbox).where(EmailOutbox.to == email)
    outbox_item = (await session.exec(statement_outbox)).first()
    assert outbox_item is not None
    assert outbox_item.status == "PENDING"


@pytest.mark.asyncio
async def test_request_magic_link_non_allowlisted_email(client, session: AsyncSession):
    # Arrange
    email = "notallowed@example.com"  # NOT present in ALLOWED_EMAILS env

    # Act
    response = await client.post("/auth/magic-link/request", json={"email": email})

    # Assert
    assert response.status_code == 202
    assert response.json()["detail"] == "If the address is registered, a link has been sent"

    # Verify no user was created
    statement = select(User).where(User.email == email)
    user = (await session.exec(statement)).first()
    assert user is None

    # Verify no email was enqueued in outbox
    statement_outbox = select(EmailOutbox).where(EmailOutbox.to == email)
    outbox_item = (await session.exec(statement_outbox)).first()
    assert outbox_item is None

    # Verify no magic link row in DB
    statement_link = select(MagicLink).where(MagicLink.email == email)
    link = (await session.exec(statement_link)).first()
    assert link is None


@pytest.mark.asyncio
async def test_request_magic_link_allowlist_case_insensitive(client, session: AsyncSession):
    # Arrange
    mixed_email = "MiXeD@ExAmPlE.cOm"  # mixed@example.com is present in ALLOWED_EMAILS env

    # Act
    response = await client.post("/auth/magic-link/request", json={"email": mixed_email})

    # Assert
    assert response.status_code == 202

    # Verify user was created with normalized lowercase email
    statement = select(User).where(User.email == "mixed@example.com")
    user = (await session.exec(statement)).first()
    assert user is not None
    assert user.email == "mixed@example.com"

    # Verify email was enqueued in outbox to lowercase email
    statement_outbox = select(EmailOutbox).where(EmailOutbox.to == "mixed@example.com")
    outbox_item = (await session.exec(statement_outbox)).first()
    assert outbox_item is not None
    assert outbox_item.status == "PENDING"


@pytest.mark.asyncio
async def test_request_magic_link_replaces_old_link(client, make_user, session: AsyncSession):
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
    session.expire_all()
    result = await session.exec(statement)
    links = result.all()

    assert len(links) == 1
    assert links[0].token_hash != hash1


@pytest.mark.asyncio
async def test_confirm_magic_link_success(client, make_user, session: AsyncSession):
    # Arrange
    email = "alice@example.com"
    await make_user(email)

    # Generate link via request
    await client.post("/auth/magic-link/request", json={"email": email})

    # Get token from outbox
    statement_outbox = select(EmailOutbox).where(EmailOutbox.to == email)
    outbox_item = (await session.exec(statement_outbox)).first()
    assert outbox_item is not None
    url_line = [line for line in outbox_item.body.split("\n") if "token=" in line][0]
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
    token_hash = hash_token(raw_token)
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
async def test_confirm_magic_link_reused(client, make_user, session: AsyncSession):
    # Arrange
    email = "reused@example.com"
    await make_user(email)

    # Request link
    await client.post("/auth/magic-link/request", json={"email": email})

    # Get token from outbox
    statement_outbox = select(EmailOutbox).where(EmailOutbox.to == email)
    outbox_item = (await session.exec(statement_outbox)).first()
    assert outbox_item is not None
    url_line = [line for line in outbox_item.body.split("\n") if "token=" in line][0]
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
