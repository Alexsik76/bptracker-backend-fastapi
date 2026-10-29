import asyncio
import logging
from datetime import UTC, datetime

import pytest
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from activity.models import ErrorEvent, RequestEvent
from activity.writer import (
    clear_activity_queue,
    flush_activity_queue,
    get_activity_queue,
    record_activity_event,
    run_activity_writer,
)
from config import get_settings


@pytest.mark.asyncio
async def test_flush_activity_queue_direct(session: AsyncSession):
    clear_activity_queue()
    now = datetime.now(UTC)

    req = RequestEvent(
        occurred_at=now,
        user_id=None,
        client="other",
        module="auth",
        method="GET",
        route="/auth/test",
        status_code=200,
        duration_ms=12,
    )
    err = ErrorEvent(
        occurred_at=now,
        user_id=None,
        method="POST",
        route="/auth/crash",
        exception_type="RuntimeError",
        message="bad",
        traceback="traceback details",
    )

    record_activity_event(req)
    record_activity_event(err)

    flushed = await flush_activity_queue(session)
    assert flushed == 2

    # Verify both are in DB
    db_reqs = (await session.exec(select(RequestEvent))).all()
    assert len(db_reqs) == 1
    assert db_reqs[0].route == "/auth/test"

    db_errs = (await session.exec(select(ErrorEvent))).all()
    assert len(db_errs) == 1
    assert db_errs[0].exception_type == "RuntimeError"

    # Queue should be empty now
    assert await flush_activity_queue(session) == 0


@pytest.mark.asyncio
async def test_flush_activity_queue_max_items(session: AsyncSession):
    clear_activity_queue()
    now = datetime.now(UTC)

    for i in range(5):
        record_activity_event(
            RequestEvent(
                occurred_at=now,
                user_id=None,
                client="other",
                module="test",
                method="GET",
                route=f"/test/{i}",
                status_code=200,
                duration_ms=5,
            )
        )

    flushed_first = await flush_activity_queue(session, max_items=3)
    assert flushed_first == 3

    flushed_second = await flush_activity_queue(session, max_items=10)
    assert flushed_second == 2


@pytest.mark.asyncio
async def test_writer_queue_overflow_drops_event_and_logs(caplog):
    clear_activity_queue()
    queue = get_activity_queue()
    now = datetime.now(UTC)

    # Fill queue to capacity (10000)
    # To avoid creating 10000 individual models slowly, use dummy items in queue directly
    dummy = RequestEvent(
        occurred_at=now,
        user_id=None,
        client="other",
        module="test",
        method="GET",
        route="/test",
        status_code=200,
        duration_ms=1,
    )
    while not queue.full():
        queue.put_nowait(dummy)

    with caplog.at_level(logging.WARNING):
        # Adding to full queue should drop and return False
        result = record_activity_event(dummy)
        assert result is False
        assert "Activity event queue is full; dropping events" in caplog.text

    clear_activity_queue()


@pytest.mark.asyncio
async def test_run_activity_writer_background_task(session: AsyncSession):
    clear_activity_queue()
    settings = get_settings()

    # Create a mock session factory that returns the test session
    class DummySessionFactory:
        def __call__(self):
            return self

        async def __aenter__(self):
            return session

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    dummy_factory = DummySessionFactory()

    writer_task = asyncio.create_task(
        run_activity_writer(session_factory=dummy_factory, settings=settings)
    )

    now = datetime.now(UTC)
    record_activity_event(
        RequestEvent(
            occurred_at=now,
            user_id=None,
            client="other",
            module="writer_test",
            method="GET",
            route="/writer-test",
            status_code=200,
            duration_ms=15,
        )
    )

    # Allow task to process
    await asyncio.sleep(0.1)

    writer_task.cancel()
    try:
        await writer_task
    except asyncio.CancelledError:
        pass

    db_reqs = (await session.exec(select(RequestEvent))).all()
    assert len(db_reqs) == 1
    assert db_reqs[0].route == "/writer-test"
