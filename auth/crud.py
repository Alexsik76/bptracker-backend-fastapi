from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from auth.models import User, UserCreate
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
