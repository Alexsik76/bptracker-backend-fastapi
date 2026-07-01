from datetime import UTC, datetime
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
async def test_record_without_taken_at_sets_it_to_now(client, config_payload):
    await _setup_reminder_config(client, config_payload)
    await _create_prescription_with_item(client, when_slots=["Morning"])

    before = datetime.now(UTC)
    response = await client.post(
        "/reminders/intake-reports", json={"period": "Morning", "date": "2026-01-15"}
    )
    after = datetime.now(UTC)

    assert response.status_code == 201
    body = response.json()

    assert body["snapshot"] == [
        {"medicine": "Bisoprolol", "amount": "1", "condition": "after meal"}
    ]
    assert "user_id" not in body

    taken_at = datetime.fromisoformat(body["taken_at"])
    recorded_at = datetime.fromisoformat(body["recorded_at"])
    assert before <= taken_at <= after
    assert before <= recorded_at <= after


@pytest.mark.asyncio
async def test_record_with_explicit_taken_at_stores_it_unaltered(client, config_payload):
    await _setup_reminder_config(client, config_payload)
    explicit_taken_at = "2026-01-10T09:15:00+00:00"

    response = await client.post(
        "/reminders/intake-reports",
        json={"period": "Morning", "date": "2026-01-10", "taken_at": explicit_taken_at},
    )
    assert response.status_code == 201
    body = response.json()

    # Backend does not interpret/adjust the client-supplied moment (docs/conventions.md).
    assert datetime.fromisoformat(body["taken_at"]) == datetime.fromisoformat(explicit_taken_at)


@pytest.mark.asyncio
async def test_repeat_confirm_same_slot_overwrites_instead_of_conflicting(client, config_payload):
    await _setup_reminder_config(client, config_payload)
    payload = {
        "period": "Morning",
        "date": "2026-01-15",
        "taken_at": "2026-01-15T08:05:00+00:00",
    }

    first = await client.post("/reminders/intake-reports", json=payload)
    assert first.status_code == 201
    first_body = first.json()

    second_payload = {**payload, "taken_at": "2026-01-15T09:30:00+00:00"}
    second = await client.post("/reminders/intake-reports", json=second_payload)
    assert second.status_code == 201
    second_body = second.json()

    assert second_body["id"] == first_body["id"]
    assert datetime.fromisoformat(second_body["taken_at"]) == datetime.fromisoformat(
        "2026-01-15T09:30:00+00:00"
    )
    assert datetime.fromisoformat(second_body["recorded_at"]) >= datetime.fromisoformat(
        first_body["recorded_at"]
    )

    listing = await client.get("/reminders/intake-reports")
    assert len(listing.json()) == 1


@pytest.mark.asyncio
async def test_record_intake_succeeds_without_reminder_config(client):
    # Recording no longer depends on reminder_config existing (no "late" to compute here).
    response = await client.post(
        "/reminders/intake-reports", json={"period": "Morning", "date": "2026-01-15"}
    )
    assert response.status_code == 201


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
