import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from auth.models import MagicLink, Session
from auth.webauthn.models import ChallengePurpose, WebAuthnChallenge
from cleanup.worker import run_cleanup_worker
from config import Settings
from email_infra.models import EmailOutbox, EmailStatus


@pytest.mark.asyncio
async def test_cleanup_worker_deletes_expired_rows(session: AsyncSession, make_user):
    now = datetime.now(UTC)
    user_id = await make_user("cleanup_test@example.com")

    # 1. Magic Links
    # - Expired (expires_at < now)
    expired_link = MagicLink(
        email="expired_link@example.com",
        token_hash="hash_expired_link",
        expires_at=now - timedelta(seconds=1),
    )
    # - Fresh (expires_at > now)
    fresh_link = MagicLink(
        email="fresh_link@example.com",
        token_hash="hash_fresh_link",
        expires_at=now + timedelta(minutes=15),
    )
    session.add(expired_link)
    session.add(fresh_link)

    # 2. Webauthn Challenges
    # - Expired (expires_at < now)
    expired_challenge = WebAuthnChallenge(
        challenge=b"expired_challenge",
        purpose=ChallengePurpose.AUTHENTICATION,
        expires_at=now - timedelta(seconds=1),
    )
    # - Fresh (expires_at > now)
    fresh_challenge = WebAuthnChallenge(
        challenge=b"fresh_challenge",
        purpose=ChallengePurpose.AUTHENTICATION,
        expires_at=now + timedelta(minutes=5),
    )
    session.add(expired_challenge)
    session.add(fresh_challenge)

    # 3. Sessions
    # - Expired > 30 days ago (expires_at < now - 30 days)
    expired_session_old = Session(
        user_id=user_id,
        token_hash="hash_session_old",
        expires_at=now - timedelta(days=31),
    )
    # - Expired 1 day ago (expires_at = now - 1 day)
    expired_session_yesterday = Session(
        user_id=user_id,
        token_hash="hash_session_yesterday",
        expires_at=now - timedelta(days=1),
    )
    # - Fresh (expires_at > now)
    fresh_session = Session(
        user_id=user_id,
        token_hash="hash_session_fresh",
        expires_at=now + timedelta(days=30),
    )
    session.add(expired_session_old)
    session.add(expired_session_yesterday)
    session.add(fresh_session)

    # 4. Email Outbox
    # - SENT and older than 30 days (status = SENT, created_at < now - 30 days)
    sent_outbox_old = EmailOutbox(
        to="old_sent@example.com",
        subject="Old Sent",
        body="Body",
        status=EmailStatus.SENT,
        created_at=now - timedelta(days=31),
        next_attempt_at=now,
    )
    # - SENT and fresh (status = SENT, created_at = now - 1 day)
    sent_outbox_fresh = EmailOutbox(
        to="fresh_sent@example.com",
        subject="Fresh Sent",
        body="Body",
        status=EmailStatus.SENT,
        created_at=now - timedelta(days=1),
        next_attempt_at=now,
    )
    # - PENDING and older than 30 days -> should survive
    pending_outbox_old = EmailOutbox(
        to="old_pending@example.com",
        subject="Old Pending",
        body="Body",
        status=EmailStatus.PENDING,
        created_at=now - timedelta(days=31),
        next_attempt_at=now,
    )
    session.add(sent_outbox_old)
    session.add(sent_outbox_fresh)
    session.add(pending_outbox_old)

    await session.commit()

    # Configure session factory for worker
    session_factory = async_sessionmaker(
        bind=session.bind,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    settings = Settings(
        cleanup_interval_minutes=60,
        cleanup_worker_enabled=True,
        # Required BaseSettings
        postgres_user="test",
        postgres_password="test",
        postgres_db="test",
        jwt_secret="test",
        smtp_host="test",
        smtp_username="test",
        smtp_password="test",
        smtp_from="test",
        magic_link_base_url="test",
        export_sheets_template_url="test",
        webauthn_rp_id="test",
        webauthn_origins=["test"],
    )

    # Run worker cycle once by mocking asyncio.sleep to raise CancelledError
    with patch("asyncio.sleep", side_effect=asyncio.CancelledError):
        try:
            await run_cleanup_worker(session_factory=session_factory, settings=settings)
        except asyncio.CancelledError:
            pass

    # Expire test session cache to force re-reading from database
    session.expire_all()

    # Assertions
    # 1. Magic Links
    all_links = (await session.exec(select(MagicLink))).all()
    emails = [lnk.email for lnk in all_links]
    assert "fresh_link@example.com" in emails
    assert "expired_link@example.com" not in emails

    # 2. WebAuthn Challenges
    all_challenges = (await session.exec(select(WebAuthnChallenge))).all()
    challs = [c.challenge for c in all_challenges]
    assert b"fresh_challenge" in challs
    assert b"expired_challenge" not in challs

    # 3. Sessions
    all_sessions = (await session.exec(select(Session))).all()
    hashes = [s.token_hash for s in all_sessions]
    assert "hash_session_fresh" in hashes
    assert "hash_session_yesterday" in hashes
    assert "hash_session_old" not in hashes

    # 4. Email Outbox
    all_outbox = (await session.exec(select(EmailOutbox))).all()
    tos = [o.to for o in all_outbox]
    assert "fresh_sent@example.com" in tos
    assert "old_pending@example.com" in tos
    assert "old_sent@example.com" not in tos
