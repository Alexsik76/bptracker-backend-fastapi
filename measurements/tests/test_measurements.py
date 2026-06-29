from uuid import UUID

import pytest


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
async def test_user_cannot_see_another_users_measurements(client_factory):
    user_a = UUID("00000000-0000-0000-0000-0000000000aa")
    user_b = UUID("00000000-0000-0000-0000-0000000000bb")

    # User A creates a measurement.
    client_a = client_factory(user_a)
    create = await client_a.post("/measurements", json={"sys": 120, "dia": 80, "pulse": 60})
    assert create.status_code == 201

    # User B lists measurements and must see none of A's.
    client_b = client_factory(user_b)
    listing = await client_b.get("/measurements")
    assert listing.status_code == 200
    assert listing.json() == []


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
