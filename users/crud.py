from uuid import UUID

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from users.models import User


async def get_user(session: AsyncSession, user_id: UUID) -> User | None:
    """Return one user by ID, or None if no such user exists."""
    statement = select(User).where(User.id == user_id)
    result = await session.exec(statement)
    return result.first()


async def lock_user(session: AsyncSession, user_id: UUID) -> User | None:
    """Return one user by ID with a FOR UPDATE lock, or None if not found."""
    statement = select(User).where(User.id == user_id).with_for_update()
    result = await session.exec(statement)
    return result.first()


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    """Return one user by email, or None if no such user exists."""
    statement = select(User).where(User.email == email)
    result = await session.exec(statement)
    return result.first()


async def get_or_create_user_by_email(session: AsyncSession, email: str) -> User:
    """Return an existing user by email, or create and return one if none exists."""
    user = await get_user_by_email(session, email)
    if not user:
        user = User(email=email)
        session.add(user)
        await session.flush()
    return user


async def update_display_name(
    session: AsyncSession,
    user_id: UUID,
    display_name: str | None,
) -> User | None:
    """Update a user's display name and commit. Returns the updated user or None."""
    user = await get_user(session, user_id)
    if not user:
        return None
    user.display_name = display_name
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def delete_user(session: AsyncSession, user_id: UUID) -> bool:
    """Delete a user and commit. Returns True if deleted, False if not found."""
    user = await get_user(session, user_id)
    if not user:
        return False
    await session.delete(user)
    await session.commit()
    return True
