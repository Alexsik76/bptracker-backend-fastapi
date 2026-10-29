# conftest.py
import asyncio
import os
import subprocess
import sys
from collections.abc import AsyncGenerator
from pathlib import Path
from uuid import UUID

import psycopg
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from psycopg.errors import DuplicateDatabase
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

os.environ["EMAIL_OUTBOX_WORKER_ENABLED"] = "False"
os.environ["CLEANUP_WORKER_ENABLED"] = "False"
os.environ["ACTIVITY_WRITER_ENABLED"] = "False"
os.environ["GEMINI_API_KEY"] = "dummy-test-key-12345"
os.environ["CORS_ORIGINS"] = "http://localhost:5173"
os.environ["ADMIN_EMAILS"] = "admin@example.com"
os.environ["MAGIC_LINK_ALLOWED_BASE_URLS"] = (
    "http://localhost:3000/auth/confirm https://client.example.com/login"
)
os.environ["ALLOWED_EMAILS"] = ",".join(
    [
        "testuser@example.com",
        "profile_test@example.com",
        "allowed@example.com",
        "existing@example.com",
        "mixed@example.com",
        "new-user@example.com",
        "alice@example.com",
        "reused@example.com",
        "known@example.com",
        "happy@example.com",
        "cooldown@example.com",
        "user_a@example.com",
        "user_b@example.com",
        "kyiv@example.com",
        "utc@example.com",
        "empty@example.com",
        "body@example.com",
        "a@example.com",
        "b@example.com",
        "foo@example.com",
        "test_session@example.com",
        "admin@example.com",
    ]
)

# Models must be imported so their tables register on SQLModel.metadata.
from activity import models as _act  # noqa: F401
from auth import models as _a  # noqa: F401
from auth.deps import get_current_user_id
from auth.webauthn import models as _w  # noqa: F401
from config import get_settings
from db import get_session
from email_infra import models as _e  # noqa: F401
from main import app
from measurements import models as _m  # noqa: F401
from nutrition import models as _n  # noqa: F401
from prescriptions import models as _p  # noqa: F401
from reminders import models as _r  # noqa: F401
from users.models import User

if sys.platform == "win32":
    # psycopg (async) needs the Selector loop, not the default Proactor loop.
    # Setting the process-wide policy here (once, at collection time) makes every
    # asyncio.new_event_loop() call return a SelectorEventLoop, without going
    # through pytest-asyncio's loop_factories parametrization mechanism. That
    # mechanism sets a `.callspec` on every test item even when there is a single,
    # hidden-id factory — which trips a bug in VS Code's Python extension test-tree
    # builder (it treats any item with `.callspec` as parametrized and assumes its
    # display name contains "[", crashing when pytest hides a single-value id).
    # noinspection PyDeprecation
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# A dedicated test database URL: same server, name + "_test".
_settings = get_settings()
_test_db_name = f"{_settings.postgres_db}_test"
_test_url = _settings.database_url.set(database=_test_db_name)

test_engine = create_async_engine(_test_url, poolclass=NullPool)
test_session_factory = async_sessionmaker(
    bind=test_engine, class_=AsyncSession, expire_on_commit=False
)


def _run_docker_command(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    cmd_str = " ".join(args)
    try:
        return subprocess.run(
            args,
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"Command '{args[0]}' not found while executing: '{cmd_str}'") from exc
    except subprocess.CalledProcessError as exc:
        err = exc.stderr.strip() if exc.stderr else exc.stdout.strip()
        msg = f"Command '{cmd_str}' failed with exit code {exc.returncode}: {err}"
        raise RuntimeError(msg) from exc


@pytest.fixture(scope="session", autouse=True)
def manage_database():
    started_by_session = False
    repo_root = Path(__file__).parent.resolve()

    if not os.environ.get("CI"):
        ps_result = _run_docker_command(
            ["docker", "compose", "ps", "--status", "running", "-q", "db"],
            cwd=repo_root,
        )
        if not ps_result.stdout.strip():
            _run_docker_command(
                ["docker", "compose", "up", "-d", "--wait", "db"],
                cwd=repo_root,
            )
            started_by_session = True

    try:
        with psycopg.connect(
            host=_settings.postgres_host,
            port=_settings.postgres_port,
            user=_settings.postgres_user,
            password=_settings.postgres_password,
            dbname=_settings.postgres_db,
            autocommit=True,
        ) as conn:
            # noinspection SqlNoDataSourceInspection
            conn.execute(f'CREATE DATABASE "{_test_db_name}"')
    except DuplicateDatabase:
        pass

    try:
        yield
    finally:
        if started_by_session:
            _run_docker_command(
                ["docker", "compose", "stop", "db"],
                cwd=repo_root,
            )


@pytest_asyncio.fixture
async def make_user(session: AsyncSession):
    # Domain tables now FK to users.id, so any user_id a test acts as must be
    # a real row, not just an arbitrary UUID.
    async def _create(email: str) -> UUID:
        user = User(email=email)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        assert user.id is not None
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
    clients: list[AsyncClient] = []

    def _make(user_id: UUID) -> AsyncClient:
        app.dependency_overrides[get_session] = lambda: session
        app.dependency_overrides[get_current_user_id] = lambda: user_id
        new_client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        clients.append(new_client)
        return new_client

    yield _make

    for active_client in clients:
        await active_client.aclose()
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def session() -> AsyncGenerator[AsyncSession]:
    # Fresh schema for each test: create all tables, then drop them after.
    async with test_engine.begin() as conn:
        # noinspection PyTypeChecker
        await conn.run_sync(SQLModel.metadata.create_all)
    async with test_session_factory() as s:
        yield s
    async with test_engine.begin() as conn:
        # noinspection PyTypeChecker
        await conn.run_sync(SQLModel.metadata.drop_all)


@pytest_asyncio.fixture
async def client(session: AsyncSession, test_user_id: UUID) -> AsyncGenerator[AsyncClient]:
    # Route the app's DB calls through the test session, acting as a real,
    # existing user (required now that domain tables FK to users.id).
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_current_user_id] = lambda: test_user_id
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client
    app.dependency_overrides.clear()
