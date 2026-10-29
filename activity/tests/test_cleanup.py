from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from activity.models import ErrorEvent, RequestEvent
from activity.service import purge_expired_activity_events


@pytest.mark.asyncio
async def test_retention_purge(session: AsyncSession):
    now = datetime.now(UTC)

    # 1. 200 days ago (should be purged under 180-day retention)
    old_time = now - timedelta(days=200)
    old_req = RequestEvent(
        occurred_at=old_time,
        user_id=None,
        client="other",
        module="test",
        method="GET",
        route="/test/old",
        status_code=200,
        duration_ms=10,
    )
    old_err = ErrorEvent(
        occurred_at=old_time,
        user_id=None,
        method="GET",
        route="/test/old",
        exception_type="ValueError",
        message="old",
        traceback="old tb",
    )

    # 2. 100 days ago (should be retained)
    mid_time = now - timedelta(days=100)
    mid_req = RequestEvent(
        occurred_at=mid_time,
        user_id=None,
        client="other",
        module="test",
        method="GET",
        route="/test/mid",
        status_code=200,
        duration_ms=10,
    )

    # 3. 5 days ago (should be retained)
    recent_time = now - timedelta(days=5)
    recent_req = RequestEvent(
        occurred_at=recent_time,
        user_id=None,
        client="other",
        module="test",
        method="GET",
        route="/test/recent",
        status_code=200,
        duration_ms=10,
    )
    recent_err = ErrorEvent(
        occurred_at=recent_time,
        user_id=None,
        method="GET",
        route="/test/recent",
        exception_type="KeyError",
        message="recent",
        traceback="recent tb",
    )

    session.add_all([old_req, old_err, mid_req, recent_req, recent_err])
    await session.commit()

    deleted_reqs, deleted_errs = await purge_expired_activity_events(
        session, retention_days=180, now=now
    )
    await session.commit()

    assert deleted_reqs == 1
    assert deleted_errs == 1

    remaining_reqs = (await session.exec(select(RequestEvent))).all()
    assert len(remaining_reqs) == 2
    routes = {r.route for r in remaining_reqs}
    assert routes == {"/test/mid", "/test/recent"}

    remaining_errs = (await session.exec(select(ErrorEvent))).all()
    assert len(remaining_errs) == 1
    assert remaining_errs[0].exception_type == "KeyError"
