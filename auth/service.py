import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from auth import crud
from auth.models import Session
from auth.security import (
    create_access_token,
    generate_magic_token,
    generate_refresh_token,
    hash_token,
)
from config import get_settings
from email_infra import OutboxEmailSender
from users import crud as users_crud

logger = logging.getLogger(__name__)


class SessionError(Exception):
    """Base class for session exceptions."""

    pass


class InvalidOrExpiredTokenError(SessionError):
    """Raised when a refresh token is invalid, expired, or revoked."""

    pass


@dataclass
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int


async def request_magic_link(
    db_session: AsyncSession, email: str, *, base_url: str | None = None
) -> None:
    settings = get_settings()
    if email in settings.allowed_emails:
        await users_crud.get_or_create_user_by_email(db_session, email)
        raw_token = generate_magic_token()
        token_hash = hash_token(raw_token)
        expires_at = datetime.now(UTC) + timedelta(minutes=settings.magic_link_ttl_minutes)

        await crud.upsert_magic_link(
            session=db_session,
            email=email,
            token_hash=token_hash,
            expires_at=expires_at,
        )

        effective_base_url = base_url or settings.magic_link_base_url
        magic_link = f"{effective_base_url}?token={raw_token}"

        subject = "Your Magic Login Link"
        text = (
            f"Hello,\n\n"
            f"Use the link below to log in to your account:\n"
            f"{magic_link}\n\n"
            f"This link will expire in {settings.magic_link_ttl_minutes} minutes."
        )
        html = (
            f"<p>Hello,</p>"
            f"<p>Use the link below to log in to your account:</p>"
            f'<p><a href="{magic_link}">Log in to BP Tracker</a></p>'
            f"<p>This link will expire in {settings.magic_link_ttl_minutes} minutes.</p>"
        )

        email_sender = OutboxEmailSender(db_session)
        await email_sender.send(
            to=email,
            subject=subject,
            text=text,
            html=html,
        )
        await db_session.commit()


async def issue_session(
    db_session: AsyncSession,
    *,
    user_id: UUID,
    user_agent: str | None,
) -> TokenPair:
    """Create a new session row in the database and return the token pair."""
    settings = get_settings()
    raw_refresh, token_hash = generate_refresh_token()
    expires_at = datetime.now(UTC) + timedelta(days=settings.refresh_token_ttl_days)

    truncated_ua = user_agent[:255] if user_agent else None

    session_row = Session(\
        user_id=user_id,
        token_hash=token_hash,
        expires_at=expires_at,
        user_agent=truncated_ua,
    )
    db_session.add(session_row)
    await db_session.commit()
    await db_session.refresh(session_row)

    access_token = create_access_token(user_id)
    expires_in = settings.access_token_expire_minutes * 60

    return TokenPair(
        access_token=access_token,
        refresh_token=raw_refresh,
        expires_in=expires_in,
    )


async def rotate_session(
    db_session: AsyncSession,
    *,
    raw_token: str,
    user_agent: str | None,
) -> TokenPair:
    """Rotate the presented refresh token with a new token pair and access token."""
    token_hash = hash_token(raw_token)

    # Lock the session row to prevent race conditions where concurrent refresh requests
    # using the same token bypass the revoked_at check and create duplicate active session families.
    statement = select(Session).where(Session.token_hash == token_hash).with_for_update()
    result = await db_session.exec(statement)
    session_row = result.first()

    if not session_row:
        raise InvalidOrExpiredTokenError()

    if session_row.revoked_at is not None:
        # When a refresh token that has already been revoked is presented again, it indicates
        # that the token has been leaked and reused (either by the legitimate user or an attacker).
        # To prevent further unauthorized access, we must invalidate all active sessions for this
        # user as the entire token family is compromised.
        logger.warning(
            "Compromise detected: Revoked refresh token reused for user %s. "
            "Invalidating all sessions.",
            session_row.user_id,
        )
        await revoke_all_user_sessions(db_session, user_id=session_row.user_id)
        await db_session.commit()
        raise InvalidOrExpiredTokenError()

    now_utc = datetime.now(UTC)
    if session_row.expires_at <= now_utc:
        raise InvalidOrExpiredTokenError()

    # Mark the old session as revoked
    session_row.revoked_at = now_utc
    db_session.add(session_row)

    # Issue a new session row in the same transaction
    settings = get_settings()
    new_raw_refresh, new_token_hash = generate_refresh_token()
    new_expires_at = now_utc + timedelta(days=settings.refresh_token_ttl_days)

    truncated_ua = user_agent[:255] if user_agent else None

    new_session_row = Session(
        user_id=session_row.user_id,
        token_hash=new_token_hash,
        expires_at=new_expires_at,
        user_agent=truncated_ua,
    )
    db_session.add(new_session_row)
    await db_session.commit()

    access_token = create_access_token(session_row.user_id)
    expires_in = settings.access_token_expire_minutes * 60

    return TokenPair(
        access_token=access_token,
        refresh_token=new_raw_refresh,
        expires_in=expires_in,
    )


async def revoke_session(db_session: AsyncSession, *, raw_token: str) -> None:
    """Revoke the session matching the raw refresh token. Idempotent."""
    token_hash = hash_token(raw_token)
    statement = select(Session).where(Session.token_hash == token_hash)
    result = await db_session.exec(statement)
    session_row = result.first()
    if session_row:
        if session_row.revoked_at is None:
            session_row.revoked_at = datetime.now(UTC)
            db_session.add(session_row)
            await db_session.commit()


async def revoke_all_user_sessions(db_session: AsyncSession, *, user_id: UUID) -> None:
    """Revoke all active sessions for a given user."""
    now_utc = datetime.now(UTC)
    statement = select(Session).where(Session.user_id == user_id, Session.revoked_at.is_(None))
    result = await db_session.exec(statement)
    active_sessions = result.all()
    for s in active_sessions:
        s.revoked_at = now_utc
        db_session.add(s)
    await db_session.commit()


async def list_active_sessions(db_session: AsyncSession, *, user_id: UUID) -> list[Session]:
    """Retrieve all non-revoked, unexpired sessions for a user."""
    now_utc = datetime.now(UTC)
    statement = (
        select(Session)
        .where(
            Session.user_id == user_id,
            Session.revoked_at.is_(None),
            Session.expires_at > now_utc,
        )
        .order_by(Session.created_at.desc())
    )
    result = await db_session.exec(statement)
    return list(result.all())
