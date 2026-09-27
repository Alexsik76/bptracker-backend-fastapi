from datetime import UTC, datetime, timedelta

from sqlmodel import delete, select
from sqlmodel.ext.asyncio.session import AsyncSession

from auth.models import MagicLink, Session


async def upsert_magic_link(
    session: AsyncSession, email: str, token_hash: str, expires_at: datetime
) -> MagicLink:
    statement = select(MagicLink).where(MagicLink.email == email)
    result = await session.exec(statement)
    link = result.first()
    if link:
        link.token_hash = token_hash
        link.created_at = datetime.now(UTC)
        link.expires_at = expires_at
    else:
        link = MagicLink(email=email, token_hash=token_hash, expires_at=expires_at)
        session.add(link)
    await session.commit()
    await session.refresh(link)
    return link


async def get_magic_link_by_hash(session: AsyncSession, token_hash: str) -> MagicLink | None:
    statement = select(MagicLink).where(MagicLink.token_hash == token_hash)
    result = await session.exec(statement)
    return result.first()


async def delete_magic_link(session: AsyncSession, link: MagicLink) -> None:
    await session.delete(link)
    await session.commit()


async def purge_expired_magic_links(session: AsyncSession, *, now: datetime | None = None) -> int:
    """Delete expired magic links. Does not commit."""
    if now is None:
        now = datetime.now(UTC)
    statement = delete(MagicLink).where(MagicLink.expires_at < now)
    result = await session.exec(statement)
    return result.rowcount


async def purge_expired_sessions(session: AsyncSession, *, now: datetime | None = None) -> int:
    """Delete sessions expired more than 30 days ago. Does not commit."""
    if now is None:
        now = datetime.now(UTC)
    # We keep recently expired session rows for a grace period so that refresh-token
    # reuse detection still recognises tokens it has retired.
    statement = delete(Session).where(Session.expires_at < now - timedelta(days=30))
    result = await session.exec(statement)
    return result.rowcount
