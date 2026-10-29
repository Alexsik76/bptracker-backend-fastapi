import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from activity.models import ErrorEvent, RequestEvent
from auth.models import Session
from auth.webauthn.models import WebAuthnCredential
from measurements.models import Measurement
from nutrition.models import GenerationLog
from prescriptions.models import Prescription, WhenSlot
from reminders.models import IntakeReport
from users.models import User


@pytest.mark.asyncio
async def test_admin_endpoints_forbidden_for_non_admin(client_factory, make_user):
    user_id = await make_user("testuser@example.com")
    regular_client = client_factory(user_id)
    endpoints = [
        "/admin/stats/usage",
        "/admin/stats/overview",
        "/admin/events",
        "/admin/errors",
        f"/admin/errors/{uuid.uuid4()}",
    ]
    for url in endpoints:
        response = await regular_client.get(url)
        assert response.status_code == 403
        assert response.json()["detail"] == "Admin only"


@pytest.mark.asyncio
async def test_admin_stats_usage(session: AsyncSession, client_factory, make_user):
    admin_id = await make_user("admin@example.com")
    admin_client = client_factory(admin_id)
    user_a = await make_user("alice@example.com")
    user_b = await make_user("bob@example.com")

    t1 = datetime(2026, 10, 20, 10, 0, tzinfo=UTC)
    t2 = datetime(2026, 10, 20, 11, 0, tzinfo=UTC)
    t3 = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)

    events = [
        # user_a, module="measurements", client="ios", status=200, duration=10
        RequestEvent(
            occurred_at=t1,
            user_id=user_a,
            client="ios",
            module="measurements",
            method="GET",
            route="/measurements/{measurement_id}",
            status_code=200,
            duration_ms=10,
        ),
        # user_a, module="measurements", client="ios", status=500 (server error), duration=50
        RequestEvent(
            occurred_at=t2,
            user_id=user_a,
            client="ios",
            module="measurements",
            method="POST",
            route="/measurements",
            status_code=500,
            duration_ms=50,
        ),
        # user_a, module="nutrition", client="android", status=400 (client error), duration=20
        RequestEvent(
            occurred_at=t2,
            user_id=user_a,
            client="android",
            module="nutrition",
            method="GET",
            route="/nutrition/dishes",
            status_code=400,
            duration_ms=20,
        ),
        # user_b, module="measurements", client="other", status=404 (client error), duration=30
        RequestEvent(
            occurred_at=t3,
            user_id=user_b,
            client="other",
            module="measurements",
            method="GET",
            route="/measurements/{measurement_id}",
            status_code=404,
            duration_ms=30,
        ),
        # anonymous (user_id=None), module="auth", client="other", status=200, duration=5
        RequestEvent(
            occurred_at=t3,
            user_id=None,
            client="other",
            module="auth",
            method="POST",
            route="/auth/magic-link",
            status_code=200,
            duration_ms=5,
        ),
    ]
    session.add_all(events)
    await session.commit()

    # 1. group_by=module (default)
    res = await admin_client.get("/admin/stats/usage?group_by=module")
    assert res.status_code == 200
    data = res.json()
    # measurements (3 requests), nutrition (1 request), auth (1 request)
    assert len(data) == 3
    assert data[0]["key"] == "measurements"
    assert data[0]["requests"] == 3
    assert data[0]["errors"] == 1  # status 500
    assert data[0]["client_errors"] == 1  # status 404
    assert data[0]["avg_ms"] == 30.0  # (10 + 50 + 30) / 3 = 30.0

    # 2. group_by=user: label should be email!
    res_user = await admin_client.get("/admin/stats/usage?group_by=user")
    assert res_user.status_code == 200
    user_data = res_user.json()
    assert len(user_data) == 3  # user_a (3), user_b (1), anonymous (1)
    # Ordered by requests desc
    assert user_data[0]["key"] == str(user_a)
    assert user_data[0]["label"] == "alice@example.com"
    assert user_data[0]["requests"] == 3
    assert user_data[0]["errors"] == 1
    assert user_data[0]["client_errors"] == 1

    # Check user_b
    b_stat = next(d for d in user_data if d["key"] == str(user_b))
    assert b_stat["label"] == "bob@example.com"
    assert b_stat["requests"] == 1

    # Check anonymous
    anon_stat = next(d for d in user_data if d["key"] == "anonymous")
    assert anon_stat["requests"] == 1

    # 3. group_by=client
    res_client = await admin_client.get("/admin/stats/usage?group_by=client")
    assert res_client.status_code == 200
    client_data = res_client.json()
    # "ios" has 2, "other" has 2, "android" has 1
    assert len(client_data) == 3
    assert {c["key"] for c in client_data} == {"ios", "other", "android"}

    # 4. group_by=day
    res_day = await admin_client.get("/admin/stats/usage?group_by=day")
    assert res_day.status_code == 200
    day_data = res_day.json()
    # 2026-10-20 (3 requests), 2026-10-21 (2 requests)
    assert len(day_data) == 2
    assert day_data[0]["key"] == "2026-10-20"
    assert day_data[0]["requests"] == 3
    assert day_data[1]["key"] == "2026-10-21"
    assert day_data[1]["requests"] == 2

    # 5. group_by=route
    res_route = await admin_client.get("/admin/stats/usage?group_by=route")
    assert res_route.status_code == 200
    route_data = res_route.json()
    # /measurements/{measurement_id} has 2
    top_route = route_data[0]
    assert top_route["key"] == "/measurements/{measurement_id}"
    assert top_route["requests"] == 2

    # 6. Filters: user_id & module
    res_filtered = await admin_client.get(
        f"/admin/stats/usage?group_by=route&user_id={user_a}&module=measurements"
    )
    assert res_filtered.status_code == 200
    filtered_data = res_filtered.json()
    assert sum(r["requests"] for r in filtered_data) == 2


@pytest.mark.asyncio
async def test_admin_stats_overview(session: AsyncSession, client_factory, make_user):
    admin_id = await make_user("admin@example.com")
    admin_client = client_factory(admin_id)
    user_id = await make_user("user_a@example.com")
    db_user = await session.get(User, user_id)
    assert db_user is not None
    db_user.display_name = "User Alpha"
    await session.commit()

    now = datetime.now(UTC)
    within_30d = now - timedelta(days=5)
    outside_30d = now - timedelta(days=40)

    # 1. Request events: 2 within 30d, 1 outside 30d
    session.add_all(
        [
            RequestEvent(
                occurred_at=within_30d,
                user_id=user_id,
                client="ios",
                module="measurements",
                method="GET",
                route="/measurements",
                status_code=200,
                duration_ms=10,
            ),
            RequestEvent(
                occurred_at=now - timedelta(hours=1),
                user_id=user_id,
                client="ios",
                module="measurements",
                method="GET",
                route="/measurements",
                status_code=200,
                duration_ms=12,
            ),
            RequestEvent(
                occurred_at=outside_30d,
                user_id=user_id,
                client="ios",
                module="measurements",
                method="GET",
                route="/measurements",
                status_code=200,
                duration_ms=8,
            ),
        ]
    )

    # 2. Measurements: 2 total, 1 within 30d
    session.add_all(
        [
            Measurement(user_id=user_id, sys=120, dia=80, pulse=70, recorded_at=within_30d),
            Measurement(user_id=user_id, sys=130, dia=85, pulse=75, recorded_at=outside_30d),
        ]
    )

    # 3. Prescriptions: 1 active, 1 inactive
    session.add_all(
        [
            Prescription(
                user_id=user_id,
                doctor="Dr. House",
                prescribed_on=date(2026, 10, 1),
                is_active=True,
            ),
            Prescription(
                user_id=user_id,
                doctor="Dr. Watson",
                prescribed_on=date(2026, 9, 1),
                is_active=False,
            ),
        ]
    )

    # 4. Intake reports: 1 within 30d, 1 outside 30d
    session.add_all(
        [
            IntakeReport(
                user_id=user_id,
                period=WhenSlot.MORNING,
                date=date(2026, 10, 25),
                taken_at=within_30d,
                recorded_at=within_30d,
                snapshot=[],
            ),
            IntakeReport(
                user_id=user_id,
                period=WhenSlot.EVENING,
                date=date(2026, 9, 10),
                taken_at=outside_30d,
                recorded_at=outside_30d,
                snapshot=[],
            ),
        ]
    )

    # 5. Generation log: 1 within 30d, 1 outside 30d
    session.add_all(
        [
            GenerationLog(
                user_id=user_id,
                date=date(2026, 10, 25),
                scope="week",
                model="gemini",
                prompt_version="1",
                created_at=within_30d,
            ),
            GenerationLog(
                user_id=user_id,
                date=date(2026, 9, 10),
                scope="week",
                model="gemini",
                prompt_version="1",
                created_at=outside_30d,
            ),
        ]
    )

    # 6. WebAuthn credential: 1 credential
    session.add(
        WebAuthnCredential(
            user_id=user_id,
            credential_id=b"cred1",
            public_key=b"pubkey1",
            sign_count=1,
            backup_eligible=True,
            backup_state=True,
        )
    )

    # 7. Sessions: 1 active, 1 expired, 1 revoked
    session.add_all(
        [
            # Active session
            Session(
                user_id=user_id,
                token_hash="hash_active",
                expires_at=now + timedelta(days=10),
                revoked_at=None,
            ),
            # Expired session
            Session(
                user_id=user_id,
                token_hash="hash_expired",
                expires_at=now - timedelta(days=1),
                revoked_at=None,
            ),
            # Revoked session
            Session(
                user_id=user_id,
                token_hash="hash_revoked",
                expires_at=now + timedelta(days=10),
                revoked_at=now - timedelta(hours=2),
            ),
        ]
    )

    await session.commit()

    response = await admin_client.get("/admin/stats/overview")
    assert response.status_code == 200
    items = response.json()
    user_item = next(it for it in items if it["email"] == "user_a@example.com")

    assert user_item["email"] == "user_a@example.com"
    assert user_item["display_name"] == "User Alpha"
    assert user_item["last_request_at"] is not None
    assert user_item["requests_30d"] == 2
    assert user_item["measurements_total"] == 2
    assert user_item["measurements_30d"] == 1
    assert user_item["prescriptions_active"] == 1
    assert user_item["intake_reports_30d"] == 1
    assert user_item["menu_generations_30d"] == 1
    assert user_item["webauthn_credentials"] == 1
    assert user_item["active_sessions"] == 1


@pytest.mark.asyncio
async def test_admin_events_endpoint(session: AsyncSession, client_factory, make_user):
    admin_id = await make_user("admin@example.com")
    admin_client = client_factory(admin_id)
    user_id = await make_user("user_b@example.com")
    base_time = datetime(2026, 10, 20, 10, 0, tzinfo=UTC)

    events = [
        RequestEvent(
            occurred_at=base_time + timedelta(minutes=i),
            user_id=user_id,
            client="okhttp" if i % 2 == 0 else "ios",
            module="auth" if i < 5 else "measurements",
            method="GET",
            route=f"/route/{i}",
            status_code=200 if i < 8 else 400,
            duration_ms=10 * i,
        )
        for i in range(10)
    ]
    session.add_all(events)
    await session.commit()

    # Pagination: limit=4, offset=0 (newest first: i=9, 8, 7, 6)
    res = await admin_client.get("/admin/events?limit=4&offset=0")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] >= 10
    assert data["limit"] == 4
    assert data["offset"] == 0
    assert len(data["items"]) == 4
    assert data["items"][0]["route"] == "/route/9"
    assert data["items"][1]["route"] == "/route/8"

    # Filter by module
    res_mod = await admin_client.get("/admin/events?module=auth")
    assert res_mod.status_code == 200
    assert res_mod.json()["total"] == 5

    # Filter by status range
    res_status = await admin_client.get("/admin/events?status_min=400&status_max=499")
    assert res_status.status_code == 200
    assert res_status.json()["total"] == 2


@pytest.mark.asyncio
async def test_admin_errors_and_error_by_id(session: AsyncSession, client_factory, make_user):
    admin_id = await make_user("admin@example.com")
    admin_client = client_factory(admin_id)
    user_id = await make_user("happy@example.com")
    t1 = datetime(2026, 10, 20, 10, 0, tzinfo=UTC)
    t2 = datetime(2026, 10, 20, 11, 0, tzinfo=UTC)

    err1 = ErrorEvent(
        occurred_at=t1,
        user_id=user_id,
        method="GET",
        route="/test/first",
        exception_type="ZeroDivisionError",
        message="division by zero",
        traceback="Traceback details 1",
    )
    err2 = ErrorEvent(
        occurred_at=t2,
        user_id=user_id,
        method="POST",
        route="/test/second",
        exception_type="KeyError",
        message="'missing_key'",
        traceback="Traceback details 2",
    )
    session.add_all([err1, err2])
    await session.commit()
    await session.refresh(err1)
    await session.refresh(err2)

    # 1. List errors: newest first (err2 first, then err1)
    res = await admin_client.get("/admin/errors?limit=10&offset=0")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 2
    assert len(data["items"]) == 2
    assert data["items"][0]["id"] == str(err2.id)
    assert data["items"][1]["id"] == str(err1.id)

    # 2. Get error by id: returns full record with traceback
    res_detail = await admin_client.get(f"/admin/errors/{err1.id}")
    assert res_detail.status_code == 200
    detail = res_detail.json()
    assert detail["id"] == str(err1.id)
    assert detail["exception_type"] == "ZeroDivisionError"
    assert detail["message"] == "division by zero"
    assert detail["traceback"] == "Traceback details 1"

    # 3. Nonexistent error id -> 404
    non_existent = uuid.uuid4()
    res_404 = await admin_client.get(f"/admin/errors/{non_existent}")
    assert res_404.status_code == 404
    assert res_404.json()["detail"] == "Error event not found"
