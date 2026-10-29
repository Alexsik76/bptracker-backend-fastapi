import asyncio
import logging
import time

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from activity.models import ErrorEvent, RequestEvent
from config import Settings

logger = logging.getLogger(__name__)

_activity_queue: asyncio.Queue[RequestEvent | ErrorEvent] | None = None
_activity_queue_loop: asyncio.AbstractEventLoop | None = None
_last_drop_warning: float = 0.0


def get_activity_queue() -> asyncio.Queue[RequestEvent | ErrorEvent]:
    global _activity_queue, _activity_queue_loop
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None

    if _activity_queue is None or (
        _activity_queue_loop is not None and _activity_queue_loop is not current_loop
    ):
        _activity_queue = asyncio.Queue(maxsize=10000)
        _activity_queue_loop = current_loop
    return _activity_queue


def record_activity_event(event: RequestEvent | ErrorEvent) -> bool:
    global _last_drop_warning
    queue = get_activity_queue()
    try:
        queue.put_nowait(event)
        return True
    except asyncio.QueueFull:
        now = time.monotonic()
        if now - _last_drop_warning >= 60.0:
            _last_drop_warning = now
            logger.warning("Activity event queue is full; dropping events")
        return False


async def flush_activity_queue(
    session: AsyncSession | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    max_items: int | None = None,
) -> int:
    queue = get_activity_queue()
    items: list[RequestEvent | ErrorEvent] = []

    while not queue.empty():
        if max_items is not None and len(items) >= max_items:
            break
        try:
            item = queue.get_nowait()
            items.append(item)
            queue.task_done()
        except asyncio.QueueEmpty:
            break

    if not items:
        return 0

    if session is not None:
        session.add_all(items)
        await session.commit()
    elif session_factory is not None:
        async with session_factory() as s:
            s.add_all(items)
            await s.commit()
    else:
        from db import async_session_factory

        async with async_session_factory() as s:
            s.add_all(items)
            await s.commit()

    return len(items)


def clear_activity_queue() -> None:
    queue = get_activity_queue()
    while not queue.empty():
        try:
            queue.get_nowait()
            queue.task_done()
        except asyncio.QueueEmpty:
            break


async def run_activity_writer(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,  # noqa: ARG001
) -> None:
    queue = get_activity_queue()

    while True:
        batch: list[RequestEvent | ErrorEvent] = []
        try:
            # Wait for the first item up to 2 seconds
            first_item = await asyncio.wait_for(queue.get(), timeout=2.0)
            batch.append(first_item)
            queue.task_done()

            # Batch up to 200 events or 2.0s deadline from first item
            deadline = asyncio.get_running_loop().time() + 2.0
            while len(batch) < 200:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=min(remaining, 0.1))
                    batch.append(item)
                    queue.task_done()
                except TimeoutError:
                    if queue.empty():
                        break
        except TimeoutError:
            pass
        except asyncio.CancelledError:
            # Shutdown: flush remaining items in queue before exiting cleanly
            try:
                while not queue.empty():
                    item = queue.get_nowait()
                    batch.append(item)
                    queue.task_done()
                if batch:
                    async with session_factory() as session:
                        session.add_all(batch)
                        await session.commit()
            except Exception:
                logger.exception("Failed to flush activity events on shutdown")
            raise

        if batch:
            try:
                async with session_factory() as session:
                    session.add_all(batch)
                    await session.commit()
            except Exception:
                logger.exception("Failed to flush activity batch of %d events", len(batch))
