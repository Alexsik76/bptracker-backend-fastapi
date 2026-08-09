from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlmodel.ext.asyncio.session import AsyncSession

from db import get_session
from main import app


@pytest.mark.asyncio
async def test_create_measurement_returns_id_without_user_id(client):
    response = await client.post(
        "/measurements",
        json={"sys": 120, "dia": 80, "pulse": 60},
    )

    assert response.status_code == 201
    body = response.json()

    # DB-generated id is present and time is set.
    assert "id" in body
    assert body["recorded_at"] is not None

    # The output form (MeasurementRead) must not leak user_id.
    assert "user_id" not in body


@pytest.mark.asyncio
async def test_create_measurement_rejects_out_of_range(client):
    # sys upper bound is 300; 500 must be refused before touching the DB.
    response = await client.post(
        "/measurements",
        json={"sys": 500, "dia": 80, "pulse": 60},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_user_cannot_see_another_users_measurements(client_factory, make_user):
    user_a = await make_user("a@example.com")
    user_b = await make_user("b@example.com")

    # User A creates a measurement.
    client_a = client_factory(user_a)
    create = await client_a.post("/measurements", json={"sys": 120, "dia": 80, "pulse": 60})
    assert create.status_code == 201

    # User B lists measurements and must see none of A's.
    client_b = client_factory(user_b)
    listing = await client_b.get("/measurements")
    assert listing.status_code == 200
    body = listing.json()
    assert body["items"] == []
    assert body["total"] == 0


@pytest.mark.asyncio
async def test_update_measurement_changes_only_sent_fields(client):
    created = await client.post("/measurements", json={"sys": 120, "dia": 80, "pulse": 60})
    mid = created.json()["id"]

    # Send only sys; dia and pulse must stay untouched.
    updated = await client.patch(f"/measurements/{mid}", json={"sys": 130})
    assert updated.status_code == 200
    body = updated.json()
    assert body["sys"] == 130
    assert body["dia"] == 80
    assert body["pulse"] == 60


@pytest.mark.asyncio
async def test_get_measurement_returns_it(client):
    created = await client.post("/measurements", json={"sys": 120, "dia": 80, "pulse": 60})
    mid = created.json()["id"]

    response = await client.get(f"/measurements/{mid}")
    assert response.status_code == 200
    assert response.json()["id"] == mid


@pytest.mark.asyncio
async def test_update_missing_measurement_returns_404(client):
    missing = "00000000-0000-0000-0000-000000000999"
    response = await client.patch(f"/measurements/{missing}", json={"sys": 130})
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_measurement_removes_it(client):
    created = await client.post("/measurements", json={"sys": 120, "dia": 80, "pulse": 60})
    mid = created.json()["id"]

    deleted = await client.delete(f"/measurements/{mid}")
    assert deleted.status_code == 204

    # Gone now: fetching it returns 404.
    gone = await client.get(f"/measurements/{mid}")
    assert gone.status_code == 404


@pytest.mark.asyncio
async def test_delete_missing_measurement_returns_404(client):
    missing = "00000000-0000-0000-0000-000000000999"
    response = await client.delete(f"/measurements/{missing}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_list_ignores_removed_days_param(client):
    # `days` was removed entirely and is no longer a declared parameter, so
    # FastAPI drops it silently rather than rejecting the request. Prove it
    # has no filtering effect any more: a record far outside the old 7-day
    # window must still come back when `days=7` is passed.
    now = datetime.now(UTC)
    await client.post(
        "/measurements",
        json={
            "sys": 110,
            "dia": 80,
            "pulse": 60,
            "recorded_at": (now - timedelta(days=200)).isoformat(),
        },
    )

    response = await client.get("/measurements?days=7")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert len(body["items"]) == 1


@pytest.mark.asyncio
async def test_list_no_range_returns_full_history_newest_first(client):
    now = datetime.now(UTC)
    records = [
        (110, now - timedelta(days=200)),
        (120, now - timedelta(days=3)),
        (130, now - timedelta(days=1)),  # newest
    ]
    for sys_val, ts in records:
        await client.post(
            "/measurements",
            json={"sys": sys_val, "dia": 80, "pulse": 60, "recorded_at": ts.isoformat()},
        )

    response = await client.get("/measurements")
    assert response.status_code == 200
    body = response.json()

    assert body["total"] == 3
    assert body["limit"] == 100
    assert body["offset"] == 0
    items = body["items"]
    assert len(items) == 3
    assert [item["sys"] for item in items] == [130, 120, 110]  # newest first


@pytest.mark.asyncio
async def test_list_date_from_only_is_inclusive(client):
    now = datetime.now(UTC)
    boundary = now - timedelta(days=3)
    records = [
        (110, now - timedelta(days=200)),  # excluded, before date_from
        (120, boundary),  # included, exactly on date_from (inclusive)
        (130, now - timedelta(days=1)),  # included, newest
    ]
    for sys_val, ts in records:
        await client.post(
            "/measurements",
            json={"sys": sys_val, "dia": 80, "pulse": 60, "recorded_at": ts.isoformat()},
        )

    response = await client.get("/measurements", params={"date_from": boundary.isoformat()})
    assert response.status_code == 200
    body = response.json()

    assert body["total"] == 2
    assert [item["sys"] for item in body["items"]] == [130, 120]


@pytest.mark.asyncio
async def test_list_date_to_only_is_exclusive(client):
    now = datetime.now(UTC)
    boundary = now - timedelta(days=1)
    records = [
        (110, now - timedelta(days=3)),  # included
        (120, boundary),  # excluded, exactly on date_to (exclusive)
        (130, now),  # excluded, after date_to
    ]
    for sys_val, ts in records:
        await client.post(
            "/measurements",
            json={"sys": sys_val, "dia": 80, "pulse": 60, "recorded_at": ts.isoformat()},
        )

    response = await client.get("/measurements", params={"date_to": boundary.isoformat()})
    assert response.status_code == 200
    body = response.json()

    assert body["total"] == 1
    assert [item["sys"] for item in body["items"]] == [110]


@pytest.mark.asyncio
async def test_list_date_from_and_date_to_both_supplied(client):
    now = datetime.now(UTC)
    records = [
        (110, now - timedelta(days=10)),  # excluded, before range
        (120, now - timedelta(days=5)),  # included
        (130, now - timedelta(days=2)),  # included
        (140, now),  # excluded, on/after date_to
    ]
    for sys_val, ts in records:
        await client.post(
            "/measurements",
            json={"sys": sys_val, "dia": 80, "pulse": 60, "recorded_at": ts.isoformat()},
        )

    date_from = (now - timedelta(days=6)).isoformat()
    date_to = (now - timedelta(days=1)).isoformat()
    response = await client.get(
        "/measurements", params={"date_from": date_from, "date_to": date_to}
    )
    assert response.status_code == 200
    body = response.json()

    assert body["total"] == 2
    assert [item["sys"] for item in body["items"]] == [130, 120]


@pytest.mark.asyncio
async def test_list_rejects_date_from_not_before_date_to(client):
    now = datetime.now(UTC)
    response = await client.get(
        "/measurements",
        params={"date_from": now.isoformat(), "date_to": now.isoformat()},
    )
    assert response.status_code == 422

    later_from = now + timedelta(days=1)
    response = await client.get(
        "/measurements",
        params={"date_from": later_from.isoformat(), "date_to": now.isoformat()},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_list_pagination_offset_beyond_end_returns_empty_items(client):
    now = datetime.now(UTC)
    for i in range(5):
        await client.post(
            "/measurements",
            json={
                "sys": 110 + i,
                "dia": 80,
                "pulse": 60,
                "recorded_at": (now - timedelta(days=i)).isoformat(),
            },
        )

    response = await client.get("/measurements", params={"limit": 2, "offset": 0})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 5
    assert len(body["items"]) == 2
    assert body["limit"] == 2
    assert body["offset"] == 0

    # Offset beyond the end: empty items, but total still reflects the full match.
    response = await client.get("/measurements", params={"limit": 2, "offset": 50})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 5
    assert body["items"] == []
    assert body["limit"] == 2
    assert body["offset"] == 50


@pytest.mark.asyncio
async def test_measurements_requires_auth(session: AsyncSession):
    # No get_current_user_id override here: exercise the real dependency, which
    # must reject a request with no bearer token. This is the one place we verify
    # the router<->auth wiring end-to-end (the rest of the tests use overrides).
    app.dependency_overrides[get_session] = lambda: session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/measurements")
    app.dependency_overrides.clear()
    assert resp.status_code == 401
