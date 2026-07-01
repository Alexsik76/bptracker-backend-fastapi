from uuid import UUID

import pytest


@pytest.fixture
def config_payload():
    return {
        "morning_time": "08:00:00",
        "day_time": "14:00:00",
        "evening_time": "20:00:00",
        "max_reminders": 3,
        "duration_minutes": 60,
    }


async def _setup_reminder_config(client, config_payload):
    response = await client.put("/reminders/config", json=config_payload)
    assert response.status_code == 200


async def _create_prescription_with_item(client, when_slots):
    prescription = await client.post(
        "/prescriptions", json={"doctor": "Dr. House", "prescribed_on": "2026-01-15"}
    )
    pid = prescription.json()["id"]
    await client.post(
        f"/prescriptions/{pid}/items",
        json={
            "medicine": "Bisoprolol",
            "condition": "after meal",
            "when_slots": when_slots,
            "dose_amount": "1",
            "freq_count": 1,
            "freq_period": 1,
            "freq_period_unit": "d",
            "course_type": "ongoing",
        },
    )
    return pid


@pytest.mark.asyncio
async def test_confirm_without_reminder_config_returns_404(client):
    response = await client.post(
        "/reminders/intake-reports", json={"period": "Morning", "date": "2026-01-15"}
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_confirm_creates_report_with_snapshot_from_active_prescription(
    client, config_payload
):
    await _setup_reminder_config(client, config_payload)
    await _create_prescription_with_item(client, when_slots=["Morning"])

    response = await client.post(
        "/reminders/intake-reports", json={"period": "Morning", "date": "2026-01-15"}
    )
    assert response.status_code == 201
    body = response.json()

    assert body["snapshot"] == [
        {"medicine": "Bisoprolol", "amount": "1", "condition": "after meal"}
    ]
    assert "user_id" not in body


@pytest.mark.asyncio
async def test_confirm_excludes_items_from_other_slots(client, config_payload):
    await _setup_reminder_config(client, config_payload)
    await _create_prescription_with_item(client, when_slots=["Evening"])

    response = await client.post(
        "/reminders/intake-reports", json={"period": "Morning", "date": "2026-01-15"}
    )
    assert response.status_code == 201
    assert response.json()["snapshot"] == []


@pytest.mark.asyncio
async def test_confirm_marks_late_when_window_has_passed(client, config_payload):
    await _setup_reminder_config(client, config_payload)
    # A slot on a date far in the past: its reminder window is long over by "now".
    response = await client.post(
        "/reminders/intake-reports", json={"period": "Morning", "date": "2000-01-01"}
    )
    assert response.status_code == 201
    assert response.json()["is_late"] is True


@pytest.mark.asyncio
async def test_confirm_marks_on_time_when_within_window(client, config_payload):
    await _setup_reminder_config(client, config_payload)
    # A slot on a date far in the future: its reminder window hasn't started yet.
    response = await client.post(
        "/reminders/intake-reports", json={"period": "Morning", "date": "2100-01-01"}
    )
    assert response.status_code == 201
    assert response.json()["is_late"] is False


@pytest.mark.asyncio
async def test_duplicate_confirm_same_slot_and_date_returns_409(client, config_payload):
    await _setup_reminder_config(client, config_payload)
    payload = {"period": "Morning", "date": "2026-01-15"}

    first = await client.post("/reminders/intake-reports", json=payload)
    assert first.status_code == 201

    second = await client.post("/reminders/intake-reports", json=payload)
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_get_missing_intake_report_returns_404(client):
    missing = "00000000-0000-0000-0000-000000000999"
    response = await client.get(f"/reminders/intake-reports/{missing}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_list_and_get_intake_reports(client, config_payload):
    await _setup_reminder_config(client, config_payload)
    created = await client.post(
        "/reminders/intake-reports", json={"period": "Morning", "date": "2026-01-15"}
    )
    rid = created.json()["id"]

    listing = await client.get("/reminders/intake-reports")
    assert listing.status_code == 200
    assert len(listing.json()) == 1

    fetched = await client.get(f"/reminders/intake-reports/{rid}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == rid


@pytest.mark.asyncio
async def test_user_cannot_see_another_users_intake_reports(client_factory, config_payload):
    user_a = UUID("00000000-0000-0000-0000-0000000000aa")
    user_b = UUID("00000000-0000-0000-0000-0000000000bb")

    client_a = client_factory(user_a)
    await _setup_reminder_config(client_a, config_payload)
    await client_a.post(
        "/reminders/intake-reports", json={"period": "Morning", "date": "2026-01-15"}
    )

    client_b = client_factory(user_b)
    listing = await client_b.get("/reminders/intake-reports")
    assert listing.status_code == 200
    assert listing.json() == []
