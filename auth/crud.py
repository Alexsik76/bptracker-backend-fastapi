from datetime import UTC, datetime

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from auth.models import MagicLink, User, UserCreate
from auth.security import hash_password


async def create_user(session: AsyncSession, data: UserCreate) -> User:
    """Persist a new user with a hashed password. Duplicate email raises IntegrityError
    on commit — the caller (router) maps it to a 409.
    """
    user = User(
        email=data.email,
        timezone=data.timezone,
        password_hash=hash_password(data.password),
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
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
