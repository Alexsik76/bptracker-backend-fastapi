from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest


@pytest.mark.asyncio
async def test_batch_creates_new_record(client):
    start_time = datetime(2026, 10, 31, 8, 0, 0, tzinfo=UTC)
    end_time = datetime(2026, 10, 31, 8, 30, 0, tzinfo=UTC)
    record_data = {
        "record_type": "ExerciseSessionRecord",
        "values": {"exercise_type": "running", "notes": "morning run"},
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "data_origin": "com.google.android.apps.fitness",
        "hc_record_id": "hc-exercise-001",
    }

    response = await client.post(
        "/health-records/batch",
        json={"upserts": [record_data]},
    )
    assert response.status_code == 200
    assert response.json() == {"created": 1, "updated": 0, "deleted": 0}

    # Verify via GET
    get_res = await client.get("/health-records")
    assert get_res.status_code == 200
    body = get_res.json()
    assert body["total"] == 1
    assert body["limit"] == 500
    assert body["offset"] == 0
    assert len(body["items"]) == 1

    item = body["items"][0]
    assert "id" in item
    UUID(item["id"])  # Valid UUID
    assert "user_id" not in item  # Output form does not leak user_id
    assert item["record_type"] == "ExerciseSessionRecord"
    assert item["values"] == {"exercise_type": "running", "notes": "morning run"}
    assert item["data_origin"] == "com.google.android.apps.fitness"
    assert item["hc_record_id"] == "hc-exercise-001"
    assert item["start_time"] is not None
    assert item["end_time"] is not None
    assert item["updated_at"] is not None


@pytest.mark.asyncio
async def test_batch_repeat_updates_record(client):
    start_time = datetime(2026, 10, 30, 9, 0, 0, tzinfo=UTC)
    record_data_1 = {
        "record_type": "WeightRecord",
        "values": {"weight": {"value": 80.0, "unit": "kg"}},
        "start_time": start_time.isoformat(),
        "end_time": None,
        "data_origin": "com.example.health",
        "hc_record_id": "hc-weight-1",
    }

    res1 = await client.post(
        "/health-records/batch",
        json={"upserts": [record_data_1]},
    )
    assert res1.status_code == 200
    assert res1.json() == {"created": 1, "updated": 0, "deleted": 0}

    # Repeat with updated values and origin
    record_data_2 = {
        "record_type": "WeightRecord",
        "values": {"weight": {"value": 81.5, "unit": "kg"}},
        "start_time": (start_time + timedelta(minutes=5)).isoformat(),
        "end_time": None,
        "data_origin": "com.example.health.v2",
        "hc_record_id": "hc-weight-1",
    }

    res2 = await client.post(
        "/health-records/batch",
        json={"upserts": [record_data_2]},
    )
    assert res2.status_code == 200
    assert res2.json() == {"created": 0, "updated": 1, "deleted": 0}

    # Still exactly one row, with updated data
    get_res = await client.get("/health-records")
    assert get_res.status_code == 200
    body = get_res.json()
    assert body["total"] == 1
    assert len(body["items"]) == 1

    item = body["items"][0]
    assert item["hc_record_id"] == "hc-weight-1"
    assert item["values"] == {"weight": {"value": 81.5, "unit": "kg"}}
    assert item["data_origin"] == "com.example.health.v2"


@pytest.mark.asyncio
async def test_batch_deleted_ids_removes_row(client):
    record = {
        "record_type": "StepsRecord",
        "values": {"count": 5000},
        "start_time": datetime(2026, 10, 29, 8, 0, 0, tzinfo=UTC).isoformat(),
        "end_time": datetime(2026, 10, 29, 20, 0, 0, tzinfo=UTC).isoformat(),
        "data_origin": "com.google.android.apps.fitness",
        "hc_record_id": "hc-steps-del",
    }

    # Insert
    res1 = await client.post("/health-records/batch", json={"upserts": [record]})
    assert res1.json() == {"created": 1, "updated": 0, "deleted": 0}

    # Delete existing + unknown id
    res2 = await client.post(
        "/health-records/batch",
        json={"deleted_ids": ["hc-steps-del", "unknown-id-999"]},
    )
    assert res2.status_code == 200
    assert res2.json() == {"created": 0, "updated": 0, "deleted": 1}

    # Check it is gone
    get_res = await client.get("/health-records")
    body = get_res.json()
    assert body["total"] == 0
    assert body["items"] == []


@pytest.mark.asyncio
async def test_user_cannot_see_update_or_delete_other_users_record(client_factory, make_user):
    user_a = await make_user("user_a@example.com")
    user_b = await make_user("user_b@example.com")

    shared_hc_id = "shared-hc-rec-1"

    # User A creates a record
    client_a = client_factory(user_a)
    res_a = await client_a.post(
        "/health-records/batch",
        json={
            "upserts": [
                {
                    "record_type": "HeartRateRecord",
                    "values": {"bpm": 72},
                    "start_time": datetime(2026, 10, 31, 6, 0, 0, tzinfo=UTC).isoformat(),
                    "data_origin": "com.sensor.hr",
                    "hc_record_id": shared_hc_id,
                }
            ]
        },
    )
    assert res_a.status_code == 200
    assert res_a.json() == {"created": 1, "updated": 0, "deleted": 0}

    # User B lists health records and sees none of user A's
    client_b = client_factory(user_b)
    res_b_list = await client_b.get("/health-records")
    assert res_b_list.status_code == 200
    assert res_b_list.json()["total"] == 0
    assert res_b_list.json()["items"] == []

    # User B posts a record with the same hc_record_id: User B gets their own row (created=1)
    res_b_create = await client_b.post(
        "/health-records/batch",
        json={
            "upserts": [
                {
                    "record_type": "HeartRateRecord",
                    "values": {"bpm": 95},
                    "start_time": datetime(2026, 10, 31, 7, 0, 0, tzinfo=UTC).isoformat(),
                    "data_origin": "com.sensor.hr",
                    "hc_record_id": shared_hc_id,
                }
            ]
        },
    )
    assert res_b_create.status_code == 200
    assert res_b_create.json() == {"created": 1, "updated": 0, "deleted": 0}

    # User A still has their own row with bpm: 72
    client_a = client_factory(user_a)
    a_items = (await client_a.get("/health-records")).json()["items"]
    assert len(a_items) == 1
    assert a_items[0]["values"] == {"bpm": 72}

    # User B has their own row with bpm: 95
    client_b = client_factory(user_b)
    b_items = (await client_b.get("/health-records")).json()["items"]
    assert len(b_items) == 1
    assert b_items[0]["values"] == {"bpm": 95}

    # User B deletes the shared hc_record_id: deletes User B's row only
    res_b_del = await client_b.post(
        "/health-records/batch",
        json={"deleted_ids": [shared_hc_id]},
    )
    assert res_b_del.status_code == 200
    assert res_b_del.json() == {"created": 0, "updated": 0, "deleted": 1}

    # User B has no records now
    assert (await client_b.get("/health-records")).json()["total"] == 0

    # User A's row is preserved!
    client_a = client_factory(user_a)
    a_items_after = (await client_a.get("/health-records")).json()["items"]
    assert len(a_items_after) == 1
    assert a_items_after[0]["values"] == {"bpm": 72}


@pytest.mark.asyncio
async def test_get_filters_by_type_and_dates(client):
    t1 = datetime(2026, 10, 1, 10, 0, 0, tzinfo=UTC)
    t2 = datetime(2026, 10, 5, 10, 0, 0, tzinfo=UTC)
    t3 = datetime(2026, 10, 10, 10, 0, 0, tzinfo=UTC)

    records = [
        {
            "record_type": "WeightRecord",
            "values": {"weight": {"value": 80.0, "unit": "kg"}},
            "start_time": t1.isoformat(),
            "data_origin": "com.test",
            "hc_record_id": "rec-1",
        },
        {
            "record_type": "WeightRecord",
            "values": {"weight": {"value": 81.0, "unit": "kg"}},
            "start_time": t2.isoformat(),
            "data_origin": "com.test",
            "hc_record_id": "rec-2",
        },
        {
            "record_type": "HeartRateRecord",
            "values": {"bpm": 75},
            "start_time": t3.isoformat(),
            "data_origin": "com.test",
            "hc_record_id": "rec-3",
        },
    ]

    setup_res = await client.post("/health-records/batch", json={"upserts": records})
    assert setup_res.json() == {"created": 3, "updated": 0, "deleted": 0}

    # 1. Filter by record_type
    res_weight = await client.get("/health-records", params={"record_type": "WeightRecord"})
    assert res_weight.status_code == 200
    data_weight = res_weight.json()
    assert data_weight["total"] == 2
    # Newest start_time first: rec-2 (t2) before rec-1 (t1)
    assert [item["hc_record_id"] for item in data_weight["items"]] == ["rec-2", "rec-1"]

    res_hr = await client.get("/health-records", params={"record_type": "HeartRateRecord"})
    assert res_hr.status_code == 200
    data_hr = res_hr.json()
    assert data_hr["total"] == 1
    assert data_hr["items"][0]["hc_record_id"] == "rec-3"

    # 2. Filter by date_from (inclusive): t2 onwards -> rec-3 (t3) and rec-2 (t2)
    res_from = await client.get("/health-records", params={"date_from": t2.isoformat()})
    assert res_from.status_code == 200
    data_from = res_from.json()
    assert data_from["total"] == 2
    assert [item["hc_record_id"] for item in data_from["items"]] == ["rec-3", "rec-2"]

    # 3. Filter by date_to (exclusive): before t2 -> rec-1 (t1)
    res_to = await client.get("/health-records", params={"date_to": t2.isoformat()})
    assert res_to.status_code == 200
    data_to = res_to.json()
    assert data_to["total"] == 1
    assert data_to["items"][0]["hc_record_id"] == "rec-1"

    # 4. Half-open range [date_from, date_to): [t1, t3) -> rec-2, rec-1
    res_range = await client.get(
        "/health-records",
        params={"date_from": t1.isoformat(), "date_to": t3.isoformat()},
    )
    assert res_range.status_code == 200
    data_range = res_range.json()
    assert data_range["total"] == 2
    assert [item["hc_record_id"] for item in data_range["items"]] == ["rec-2", "rec-1"]

    # 5. Pagination: limit=1, offset=1 on all records (total=3)
    res_page = await client.get("/health-records", params={"limit": 1, "offset": 1})
    assert res_page.status_code == 200
    data_page = res_page.json()
    assert data_page["total"] == 3
    assert data_page["limit"] == 1
    assert data_page["offset"] == 1
    assert len(data_page["items"]) == 1
    assert data_page["items"][0]["hc_record_id"] == "rec-2"

    # 6. Reject date_from >= date_to
    res_invalid_equal = await client.get(
        "/health-records",
        params={"date_from": t2.isoformat(), "date_to": t2.isoformat()},
    )
    assert res_invalid_equal.status_code == 422

    res_invalid_greater = await client.get(
        "/health-records",
        params={"date_from": t3.isoformat(), "date_to": t1.isoformat()},
    )
    assert res_invalid_greater.status_code == 422


@pytest.mark.asyncio
async def test_batch_combined_size_capped_at_1000(client):
    # Over 1000 items -> 422
    over_limit_ids = [f"id-{i}" for i in range(1001)]
    res_over = await client.post(
        "/health-records/batch",
        json={"deleted_ids": over_limit_ids},
    )
    assert res_over.status_code == 422

    # Exactly 1000 items -> 200
    exact_limit_ids = [f"id-{i}" for i in range(1000)]
    res_exact = await client.post(
        "/health-records/batch",
        json={"deleted_ids": exact_limit_ids},
    )
    assert res_exact.status_code == 200
    assert res_exact.json() == {"created": 0, "updated": 0, "deleted": 0}


@pytest.mark.asyncio
async def test_batch_empty_body(client):
    # Empty JSON body defaults both lists to empty
    response = await client.post("/health-records/batch", json={})
    assert response.status_code == 200
    assert response.json() == {"created": 0, "updated": 0, "deleted": 0}


@pytest.mark.asyncio
async def test_batch_duplicate_hc_record_id_in_same_request(client):
    start = datetime(2026, 10, 31, 10, 0, 0, tzinfo=UTC).isoformat()
    r1 = {
        "record_type": "WeightRecord",
        "values": {"weight": {"value": 75.0, "unit": "kg"}},
        "start_time": start,
        "data_origin": "com.test",
        "hc_record_id": "same-id",
    }
    r2 = {
        "record_type": "WeightRecord",
        "values": {"weight": {"value": 76.0, "unit": "kg"}},
        "start_time": start,
        "data_origin": "com.test",
        "hc_record_id": "same-id",
    }

    res = await client.post("/health-records/batch", json={"upserts": [r1, r2]})
    assert res.status_code == 200
    assert res.json()["created"] == 1

    get_res = await client.get("/health-records")
    items = get_res.json()["items"]
    assert len(items) == 1
    assert items[0]["values"] == {"weight": {"value": 76.0, "unit": "kg"}}
