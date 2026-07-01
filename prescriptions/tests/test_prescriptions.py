import pytest


@pytest.mark.asyncio
async def test_create_prescription_returns_id_without_user_id(client):
    response = await client.post(
        "/prescriptions",
        json={"doctor": "Dr. House", "prescribed_on": "2026-01-15"},
    )

    assert response.status_code == 201
    body = response.json()

    assert "id" in body
    assert body["created_at"] is not None
    # Defaults to active.
    assert body["is_active"] is True
    assert "user_id" not in body


@pytest.mark.asyncio
async def test_create_prescription_rejects_missing_required_field(client):
    response = await client.post("/prescriptions", json={"doctor": "Dr. House"})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_multiple_active_prescriptions_allowed(client):
    # Deliberate: no "single active" invariant — multi-doctor scenario.
    first = await client.post(
        "/prescriptions",
        json={"doctor": "Dr. House", "prescribed_on": "2026-01-15"},
    )
    second = await client.post(
        "/prescriptions",
        json={"doctor": "Dr. Wilson", "prescribed_on": "2026-02-01"},
    )
    assert first.status_code == 201
    assert second.status_code == 201

    listing = await client.get("/prescriptions")
    assert listing.status_code == 200
    assert len(listing.json()) == 2
    assert all(p["is_active"] for p in listing.json())


@pytest.mark.asyncio
async def test_user_cannot_see_another_users_prescriptions(client_factory, make_user):
    user_a = await make_user("a@example.com")
    user_b = await make_user("b@example.com")

    client_a = client_factory(user_a)
    create = await client_a.post(
        "/prescriptions", json={"doctor": "Dr. House", "prescribed_on": "2026-01-15"}
    )
    assert create.status_code == 201

    client_b = client_factory(user_b)
    listing = await client_b.get("/prescriptions")
    assert listing.status_code == 200
    assert listing.json() == []


@pytest.mark.asyncio
async def test_get_prescription_returns_it(client):
    created = await client.post(
        "/prescriptions", json={"doctor": "Dr. House", "prescribed_on": "2026-01-15"}
    )
    pid = created.json()["id"]

    response = await client.get(f"/prescriptions/{pid}")
    assert response.status_code == 200
    assert response.json()["id"] == pid


@pytest.mark.asyncio
async def test_update_prescription_changes_only_sent_fields(client):
    created = await client.post(
        "/prescriptions", json={"doctor": "Dr. House", "prescribed_on": "2026-01-15"}
    )
    pid = created.json()["id"]

    updated = await client.patch(f"/prescriptions/{pid}", json={"is_active": False})
    assert updated.status_code == 200
    body = updated.json()
    assert body["is_active"] is False
    assert body["doctor"] == "Dr. House"
    assert body["prescribed_on"] == "2026-01-15"


@pytest.mark.asyncio
async def test_update_missing_prescription_returns_404(client):
    missing = "00000000-0000-0000-0000-000000000999"
    response = await client.patch(f"/prescriptions/{missing}", json={"is_active": False})
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_missing_prescription_returns_404(client):
    missing = "00000000-0000-0000-0000-000000000999"
    response = await client.get(f"/prescriptions/{missing}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_prescription_removes_it(client):
    created = await client.post(
        "/prescriptions", json={"doctor": "Dr. House", "prescribed_on": "2026-01-15"}
    )
    pid = created.json()["id"]

    deleted = await client.delete(f"/prescriptions/{pid}")
    assert deleted.status_code == 204

    gone = await client.get(f"/prescriptions/{pid}")
    assert gone.status_code == 404


@pytest.mark.asyncio
async def test_delete_missing_prescription_returns_404(client):
    missing = "00000000-0000-0000-0000-000000000999"
    response = await client.delete(f"/prescriptions/{missing}")
    assert response.status_code == 404
