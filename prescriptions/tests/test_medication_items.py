import pytest


@pytest.fixture
def item_payload():
    return {
        "medicine": "Bisoprolol",
        "condition": "after meal",
        "when_slots": ["Morning"],
        "dose_amount": "0.5",
        "dose_unit": "tablet",
        "freq_count": 1,
        "freq_period": 1,
        "freq_period_unit": "d",
        "course_type": "ongoing",
    }


async def _create_prescription(client) -> str:
    created = await client.post(
        "/prescriptions", json={"doctor": "Dr. House", "prescribed_on": "2026-01-15"}
    )
    return created.json()["id"]


@pytest.mark.asyncio
async def test_create_medication_item_returns_id(client, item_payload):
    pid = await _create_prescription(client)

    response = await client.post(f"/prescriptions/{pid}/items", json=item_payload)
    assert response.status_code == 201
    body = response.json()

    assert "id" in body
    assert body["prescription_id"] == pid
    assert body["when_slots"] == ["Morning"]
    assert body["dose_unit"] == "tablet"


@pytest.mark.asyncio
async def test_create_medication_item_for_missing_prescription_returns_404(client, item_payload):
    missing = "00000000-0000-0000-0000-000000000999"
    response = await client.post(f"/prescriptions/{missing}/items", json=item_payload)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_create_medication_item_rejects_invalid_enum(client, item_payload):
    pid = await _create_prescription(client)
    item_payload["when_slots"] = ["Noon"]  # not a valid WhenSlot

    response = await client.post(f"/prescriptions/{pid}/items", json=item_payload)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_medication_item_without_dose_unit(client, item_payload):
    # dose_unit is optional — unit may live in the medicine name.
    pid = await _create_prescription(client)
    del item_payload["dose_unit"]

    response = await client.post(f"/prescriptions/{pid}/items", json=item_payload)
    assert response.status_code == 201
    assert response.json()["dose_unit"] is None


@pytest.mark.asyncio
async def test_list_medication_items(client, item_payload):
    pid = await _create_prescription(client)
    await client.post(f"/prescriptions/{pid}/items", json=item_payload)
    await client.post(
        f"/prescriptions/{pid}/items", json={**item_payload, "medicine": "Lisinopril"}
    )

    response = await client.get(f"/prescriptions/{pid}/items")
    assert response.status_code == 200
    assert len(response.json()) == 2


@pytest.mark.asyncio
async def test_list_medication_items_for_missing_prescription_returns_404(client):
    missing = "00000000-0000-0000-0000-000000000999"
    response = await client.get(f"/prescriptions/{missing}/items")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_medication_item_returns_it(client, item_payload):
    pid = await _create_prescription(client)
    created = await client.post(f"/prescriptions/{pid}/items", json=item_payload)
    iid = created.json()["id"]

    response = await client.get(f"/prescriptions/{pid}/items/{iid}")
    assert response.status_code == 200
    assert response.json()["id"] == iid


@pytest.mark.asyncio
async def test_update_medication_item_changes_only_sent_fields(client, item_payload):
    pid = await _create_prescription(client)
    created = await client.post(f"/prescriptions/{pid}/items", json=item_payload)
    iid = created.json()["id"]

    updated = await client.patch(f"/prescriptions/{pid}/items/{iid}", json={"dose_amount": "1"})
    assert updated.status_code == 200
    body = updated.json()
    assert body["dose_amount"] == "1"
    assert body["medicine"] == "Bisoprolol"


@pytest.mark.asyncio
async def test_update_missing_medication_item_returns_404(client):
    pid = await _create_prescription(client)
    missing = "00000000-0000-0000-0000-000000000999"

    response = await client.patch(
        f"/prescriptions/{pid}/items/{missing}", json={"dose_amount": "1"}
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_medication_item_removes_it(client, item_payload):
    pid = await _create_prescription(client)
    created = await client.post(f"/prescriptions/{pid}/items", json=item_payload)
    iid = created.json()["id"]

    deleted = await client.delete(f"/prescriptions/{pid}/items/{iid}")
    assert deleted.status_code == 204

    gone = await client.get(f"/prescriptions/{pid}/items/{iid}")
    assert gone.status_code == 404


@pytest.mark.asyncio
async def test_delete_missing_medication_item_returns_404(client):
    pid = await _create_prescription(client)
    missing = "00000000-0000-0000-0000-000000000999"

    response = await client.delete(f"/prescriptions/{pid}/items/{missing}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_prescription_cascades_to_medication_items(client, item_payload):
    # DB-level cascade: deleting the parent removes its items.
    pid = await _create_prescription(client)
    created = await client.post(f"/prescriptions/{pid}/items", json=item_payload)
    iid = created.json()["id"]

    await client.delete(f"/prescriptions/{pid}")

    # Prescription is gone, so the nested route 404s on the parent lookup —
    # this still proves the item is unreachable, not that the row was dropped.
    gone = await client.get(f"/prescriptions/{pid}/items/{iid}")
    assert gone.status_code == 404
