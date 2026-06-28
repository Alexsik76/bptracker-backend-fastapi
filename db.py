from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

from config import get_settings

settings = get_settings()

# One engine per process; it owns the connection pool. echo logs SQL in dev only.
engine = create_async_engine(settings.database_url, echo=settings.is_dev)

# expire_on_commit=False keeps ORM objects usable after commit
# (prevents implicit lazy-load -> MissingGreenlet in async code).
async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_session() -> AsyncGenerator[AsyncSession]:
    async with async_session_factory() as session:
        yield session


# Use `session: SessionDep` in endpoints instead of repeating Depends(get_session).
SessionDep = Annotated[AsyncSession, Depends(get_session)]
