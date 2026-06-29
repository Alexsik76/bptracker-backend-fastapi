import asyncio
import selectors
import sys
from collections.abc import AsyncGenerator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from config import get_settings
from db import get_session
from main import app

# Models must be imported so their tables register on SQLModel.metadata.
from measurements import models as _m  # noqa: F401

# A dedicated test database URL: same server, name + "_test".
_settings = get_settings()
_test_db_name = f"{_settings.postgres_db}_test"
_test_url = _settings.database_url.set(database=_test_db_name)

test_engine = create_async_engine(_test_url)
test_session_factory = async_sessionmaker(
    bind=test_engine, class_=AsyncSession, expire_on_commit=False
)


def pytest_asyncio_loop_factories():
    # Windows: psycopg needs the Selector loop, not the default Proactor.
    if sys.platform == "win32":
        return {"selector": lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())}
    return {"default": asyncio.new_event_loop}


@pytest_asyncio.fixture
async def session() -> AsyncGenerator[AsyncSession]:
    # Fresh schema for each test: create all tables, then drop them after.
    async with test_engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    async with test_session_factory() as s:
        yield s
    async with test_engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all)


@pytest_asyncio.fixture
async def client(session: AsyncSession) -> AsyncGenerator[AsyncClient]:
    # Route the app's DB calls through the test session.
    app.dependency_overrides[get_session] = lambda: session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
