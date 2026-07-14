from datetime import UTC, datetime

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from auth.models import MagicLink, User


async def get_or_create_user_by_email(session: AsyncSession, email: str) -> User:
    """Return an existing user by email, or create and return one if none exists."""
    user = await get_user_by_email(session, email)
    if not user:
        user = User(email=email)
        session.add(user)
        await session.flush()
    return user


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    """Return one user by email, or None if no such user exists."""
    statement = select(User).where(User.email == email)
    result = await session.exec(statement)
    return result.first()


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
