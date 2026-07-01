# conftest.py
import asyncio
import selectors
import sys
from collections.abc import AsyncGenerator
from uuid import UUID

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

# Models must be imported so their tables register on SQLModel.metadata.
from auth import models as _a  # noqa: F401
from config import get_settings
from db import get_session
from main import app
from measurements import models as _m  # noqa: F401
from measurements.router import get_current_user_id as get_measurements_current_user_id
from prescriptions import models as _p  # noqa: F401
from prescriptions.router import get_current_user_id as get_prescriptions_current_user_id
from reminders import models as _r  # noqa: F401
from reminders.router import get_current_user_id as get_reminders_current_user_id

# A dedicated test database URL: same server, name + "_test".
_settings = get_settings()
_test_db_name = f"{_settings.postgres_db}_test"
_test_url = _settings.database_url.set(database=_test_db_name)

test_engine = create_async_engine(_test_url)
test_session_factory = async_sessionmaker(
    bind=test_engine, class_=AsyncSession, expire_on_commit=False
)


@pytest_asyncio.fixture
async def client_factory(session: AsyncSession):
    # Build an HTTP client acting as a specific user. Lets one test play
    # two different users and prove they can't see each other's data.
    # All modules' auth stubs are overridden together — each router still
    # has its own get_current_user_id seam, but tests act as one caller.
    clients = []

    def _make(user_id: UUID) -> AsyncClient:
        app.dependency_overrides[get_session] = lambda: session
        app.dependency_overrides[get_measurements_current_user_id] = lambda: user_id
        app.dependency_overrides[get_prescriptions_current_user_id] = lambda: user_id
        app.dependency_overrides[get_reminders_current_user_id] = lambda: user_id
        c = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        clients.append(c)
        return c

    yield _make

    for c in clients:
        await c.aclose()
    app.dependency_overrides.clear()


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
    # No user_id override needed: all routers' dev stubs already default
    # to the same hardcoded UUID.
    app.dependency_overrides[get_session] = lambda: session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
