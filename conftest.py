# conftest.py
import asyncio
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
from auth.deps import get_current_user_id
from auth.models import User
from auth.security import hash_password
from auth.webauthn import models as _w  # noqa: F401
from config import get_settings
from db import get_session
from main import app
from measurements import models as _m  # noqa: F401
from prescriptions import models as _p  # noqa: F401
from reminders import models as _r  # noqa: F401

if sys.platform == "win32":
    # psycopg (async) needs the Selector loop, not the default Proactor loop.
    # Setting the process-wide policy here (once, at collection time) makes every
    # asyncio.new_event_loop() call return a SelectorEventLoop, without going
    # through pytest-asyncio's loop_factories parametrization mechanism. That
    # mechanism sets a `.callspec` on every test item even when there is a single,
    # hidden-id factory — which trips a bug in VS Code's Python extension test-tree
    # builder (it treats any item with `.callspec` as parametrized and assumes its
    # display name contains "[", crashing when pytest hides a single-value id).
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# A dedicated test database URL: same server, name + "_test".
_settings = get_settings()
_test_db_name = f"{_settings.postgres_db}_test"
_test_url = _settings.database_url.set(database=_test_db_name)

test_engine = create_async_engine(_test_url)
test_session_factory = async_sessionmaker(
    bind=test_engine, class_=AsyncSession, expire_on_commit=False
)


@pytest_asyncio.fixture
async def make_user(session: AsyncSession):
    # Domain tables now FK to users.id, so any user_id a test acts as must be
    # a real row, not just an arbitrary UUID.
    async def _create(email: str) -> UUID:
        user = User(email=email, password_hash=hash_password("test-password-123"))
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user.id

    return _create


@pytest_asyncio.fixture
async def test_user_id(make_user) -> UUID:
    return await make_user("testuser@example.com")


@pytest_asyncio.fixture
async def client_factory(session: AsyncSession):
    # Build an HTTP client acting as a specific user. Lets one test play
    # two different users and prove they can't see each other's data.
    # All three domain routers depend on the same auth.deps.get_current_user_id,
    # so overriding it once covers all of them.
    clients = []

    def _make(user_id: UUID) -> AsyncClient:
        app.dependency_overrides[get_session] = lambda: session
        app.dependency_overrides[get_current_user_id] = lambda: user_id
        c = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        clients.append(c)
        return c

    yield _make

    for c in clients:
        await c.aclose()
    app.dependency_overrides.clear()


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
async def client(session: AsyncSession, test_user_id: UUID) -> AsyncGenerator[AsyncClient]:
    # Route the app's DB calls through the test session, acting as a real,
    # existing user (required now that domain tables FK to users.id).
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_current_user_id] = lambda: test_user_id
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
