import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from main import app


@pytest.mark.asyncio
async def test_metadata_lists_registered_tables_and_hides_secrets(client_factory, make_user):
    admin_id = await make_user("admin@example.com")
    client = client_factory(admin_id)

    response = await client.get("/admin/tables")
    assert response.status_code == 200
    tables = response.json()
    assert isinstance(tables, list)

    table_names = {t["name"] for t in tables}

    # Expected registered tables
    expected_tables = {
        "users",
        "measurements",
        "prescriptions",
        "medication_items",
        "reminder_config",
        "intake_reports",
        "dishes",
        "dish_components",
        "frequent_dishes",
        "frequent_dish_components",
        "generation_log",
        "menus",
        "menu_days",
        "menu_items",
        "menu_notes",
        "off_plan_entries",
        "pinned_meals",
        "pinned_meal_items",
        "products",
        "nutrition_profiles",
        "recipes",
        "recipe_products",
        "sessions",
        "webauthn_credentials",
        "email_outbox",
        "export_state",
    }
    assert expected_tables.issubset(table_names)

    # Magic links and WebAuthn challenges must NOT be registered
    assert "magic_links" not in table_names
    assert "webauthn_challenges" not in table_names

    tables_by_name = {t["name"]: t for t in tables}

    # Module checks and no label
    for t in tables:
        assert "label" not in t

    assert tables_by_name["users"]["module"] == "users"
    assert tables_by_name["sessions"]["module"] == "auth"
    assert tables_by_name["dishes"]["module"] == "nutrition"

    # Sorted by (module, name)
    assert tables == sorted(tables, key=lambda t: (t["module"], t["name"]))

    # Permission checks
    assert tables_by_name["users"]["can_create"] is True
    assert tables_by_name["users"]["can_update"] is True
    assert tables_by_name["users"]["can_delete"] is True

    assert tables_by_name["generation_log"]["can_create"] is False
    assert tables_by_name["generation_log"]["can_update"] is False
    assert tables_by_name["generation_log"]["can_delete"] is True

    assert tables_by_name["email_outbox"]["can_create"] is False
    assert tables_by_name["email_outbox"]["can_update"] is False
    assert tables_by_name["email_outbox"]["can_delete"] is False

    assert tables_by_name["export_state"]["can_create"] is False
    assert tables_by_name["export_state"]["can_update"] is False
    assert tables_by_name["export_state"]["can_delete"] is False

    # Hidden columns check: no table exposes columns containing hash, secret, or public_key
    for t in tables:
        col_names = {c["name"] for c in t["columns"]}
        for col_name in col_names:
            lower = col_name.lower()
            assert "hash" not in lower, f"Table {t['name']} exposes hash column {col_name}"
            assert "secret" not in lower, f"Table {t['name']} exposes secret column {col_name}"
            assert "public_key" not in lower, (
                f"Table {t['name']} exposes public_key column {col_name}"
            )

    # Specifically verify sessions and webauthn_credentials
    session_cols = {c["name"] for c in tables_by_name["sessions"]["columns"]}
    assert "token_hash" not in session_cols

    webauthn_cols = {c["name"] for c in tables_by_name["webauthn_credentials"]["columns"]}
    assert "public_key" not in webauthn_cols

    # Read-only column checks in metadata
    user_cols = {c["name"]: c for c in tables_by_name["users"]["columns"]}
    assert user_cols["id"]["read_only"] is True
    assert user_cols["created_at"]["read_only"] is True
    assert user_cols["email"]["read_only"] is False

    dish_cols = {c["name"]: c for c in tables_by_name["dishes"]["columns"]}
    assert dish_cols["id"]["read_only"] is True
    assert dish_cols["created_at"]["read_only"] is True
    assert dish_cols["updated_at"]["read_only"] is True

    meas_cols = {c["name"]: c for c in tables_by_name["measurements"]["columns"]}
    assert meas_cols["id"]["read_only"] is True
    assert meas_cols["recorded_at"]["read_only"] is False

    intake_cols = {c["name"]: c for c in tables_by_name["intake_reports"]["columns"]}
    assert intake_cols["id"]["read_only"] is True
    assert intake_cols["recorded_at"]["read_only"] is True

    menu_day_cols = {c["name"]: c for c in tables_by_name["menu_days"]["columns"]}
    assert "id" not in menu_day_cols
    assert menu_day_cols["created_at"]["read_only"] is True


@pytest.mark.asyncio
async def test_list_measurements_pagination_ordering_filter_search(client_factory, make_user):
    admin_id = await make_user("admin@example.com")
    user_id = await make_user("testuser@example.com")
    client = client_factory(admin_id)

    # Insert 5 measurements
    for sys_val, dia_val, pulse_val in [
        (110, 70, 60),
        (120, 80, 70),
        (130, 85, 75),
        (140, 90, 80),
        (150, 95, 85),
    ]:
        resp = await client.post(
            "/admin/tables/measurements/rows",
            json={
                "values": {
                    "user_id": str(user_id),
                    "sys": sys_val,
                    "dia": dia_val,
                    "pulse": pulse_val,
                }
            },
        )
        assert resp.status_code == 201

    # 1. Pagination: limit and offset
    resp = await client.get("/admin/tables/measurements/rows?limit=2&offset=0")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 5
    assert len(data["items"]) == 2
    assert data["limit"] == 2
    assert data["offset"] == 0

    resp = await client.get("/admin/tables/measurements/rows?limit=2&offset=2")
    assert resp.status_code == 200
    data2 = resp.json()
    assert len(data2["items"]) == 2
    assert data2["items"] != data["items"]

    # 2. Ordering: order_by sys asc & desc
    resp_asc = await client.get("/admin/tables/measurements/rows?order_by=sys&desc=false")
    assert resp_asc.status_code == 200
    sys_asc = [item["sys"] for item in resp_asc.json()["items"]]
    assert sys_asc == [110, 120, 130, 140, 150]

    resp_desc = await client.get("/admin/tables/measurements/rows?order_by=sys&desc=true")
    assert resp_desc.status_code == 200
    sys_desc = [item["sys"] for item in resp_desc.json()["items"]]
    assert sys_desc == [150, 140, 130, 120, 110]

    # 3. Filter: equality filter on sys
    resp_filter = await client.get("/admin/tables/measurements/rows?filter=sys:130")
    assert resp_filter.status_code == 200
    items = resp_filter.json()["items"]
    assert len(items) == 1
    assert items[0]["sys"] == 130
    assert items[0]["dia"] == 85

    # 4. Search on measurements (no string columns -> returns 0 items, does not fail)
    resp_search = await client.get("/admin/tables/measurements/rows?search=hello")
    assert resp_search.status_code == 200
    assert resp_search.json()["total"] == 0

    # Search on a table with string columns (users)
    resp_search_users = await client.get("/admin/tables/users/rows?search=admin")
    assert resp_search_users.status_code == 200
    user_items = resp_search_users.json()["items"]
    assert len(user_items) >= 1
    assert any(u["email"] == "admin@example.com" for u in user_items)


@pytest.mark.asyncio
async def test_create_update_delete_measurement(client_factory, make_user):
    test_day = datetime.now(UTC).date() + timedelta(days=2)
    admin_id = await make_user("admin@example.com")
    user_id = await make_user("testuser@example.com")
    client = client_factory(admin_id)

    # Create measurement
    create_resp = await client.post(
        "/admin/tables/measurements/rows",
        json={"values": {"user_id": str(user_id), "sys": 125, "dia": 82, "pulse": 72}},
    )
    assert create_resp.status_code == 201
    created = create_resp.json()
    assert created["user_id"] == str(user_id)
    assert created["sys"] == 125
    assert created["dia"] == 82
    assert created["pulse"] == 72
    measurement_id = created["id"]
    assert measurement_id is not None
    # Serialized datetime with timezone offset
    assert "recorded_at" in created and created["recorded_at"] is not None
    assert "+" in created["recorded_at"] or created["recorded_at"].endswith("Z")

    # Partial update measurement (including editable recorded_at)
    new_recorded_at = f"{test_day}T14:30:00+00:00"
    update_resp = await client.patch(
        "/admin/tables/measurements/rows",
        json={
            "pk": {"id": measurement_id},
            "values": {"sys": 138, "recorded_at": new_recorded_at},
        },
    )
    assert update_resp.status_code == 200
    updated = update_resp.json()
    assert updated["id"] == measurement_id
    assert updated["sys"] == 138
    assert updated["dia"] == 82
    assert updated["pulse"] == 72
    assert updated["recorded_at"] == new_recorded_at

    # Delete measurement
    delete_resp = await client.post(
        "/admin/tables/measurements/rows/delete",
        json={"pk": {"id": measurement_id}},
    )
    assert delete_resp.status_code == 204

    # Verify deleted
    list_resp = await client.get(f"/admin/tables/measurements/rows?filter=id:{measurement_id}")
    assert list_resp.status_code == 200
    assert list_resp.json()["total"] == 0


@pytest.mark.asyncio
async def test_read_only_and_hidden_columns_rejected(client_factory, make_user):
    test_day = datetime.now(UTC).date() + timedelta(days=2)
    admin_id = await make_user("admin@example.com")
    user_id = await make_user("testuser@example.com")
    client = client_factory(admin_id)

    # Reject read-only column 'id' on create
    resp = await client.post(
        "/admin/tables/measurements/rows",
        json={
            "values": {
                "id": str(uuid.uuid4()),
                "user_id": str(user_id),
                "sys": 120,
                "dia": 80,
                "pulse": 70,
            }
        },
    )
    assert resp.status_code == 422
    assert "read-only" in resp.json()["detail"].lower()

    # Reject read-only column 'updated_at' on dishes create
    now_iso = datetime.now(UTC).isoformat()
    resp = await client.post(
        "/admin/tables/dishes/rows",
        json={
            "values": {
                "name": "Test Dish",
                "user_id": str(user_id),
                "updated_at": now_iso,
            }
        },
    )
    assert resp.status_code == 422
    assert "read-only" in resp.json()["detail"].lower()

    # Reject read-only column 'updated_at' on dishes patch
    resp = await client.patch(
        "/admin/tables/dishes/rows",
        json={
            "pk": {"id": str(uuid.uuid4())},
            "values": {"updated_at": now_iso},
        },
    )
    assert resp.status_code == 422
    assert "read-only" in resp.json()["detail"].lower()

    # Reject read-only column 'recorded_at' on intake_reports create
    resp = await client.post(
        "/admin/tables/intake_reports/rows",
        json={
            "values": {
                "recorded_at": now_iso,
                "user_id": str(user_id),
                "period": "MORNING",
                "date": str(test_day),
                "taken_at": now_iso,
                "snapshot": [],
            }
        },
    )
    assert resp.status_code == 422
    assert "read-only" in resp.json()["detail"].lower()

    # Reject read-only column 'recorded_at' on intake_reports patch
    resp = await client.patch(
        "/admin/tables/intake_reports/rows",
        json={
            "pk": {"id": str(uuid.uuid4())},
            "values": {"recorded_at": now_iso},
        },
    )
    assert resp.status_code == 422
    assert "read-only" in resp.json()["detail"].lower()

    # Reject hidden column in values
    resp = await client.post(
        "/admin/tables/users/rows",
        json={"values": {"email": "secret@example.com", "token_hash": "secret123"}},
    )
    assert resp.status_code == 422

    # Reject unknown column in values
    resp = await client.post(
        "/admin/tables/measurements/rows",
        json={
            "values": {
                "user_id": str(user_id),
                "sys": 120,
                "dia": 80,
                "pulse": 70,
                "nonexistent_column": "bad",
            }
        },
    )
    assert resp.status_code == 422
    assert "unknown column" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_naive_datetime_rejected(client_factory, make_user):
    test_day = datetime.now(UTC).date() + timedelta(days=2)
    admin_id = await make_user("admin@example.com")
    user_id = await make_user("testuser@example.com")
    client = client_factory(admin_id)

    # intake_reports has writable taken_at datetime column
    naive_dt = f"{test_day}T10:00:00"  # No timezone offset
    resp = await client.post(
        "/admin/tables/intake_reports/rows",
        json={
            "values": {
                "user_id": str(user_id),
                "period": "MORNING",
                "date": str(test_day),
                "taken_at": naive_dt,
                "snapshot": [],
            }
        },
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert "taken_at" in detail.lower()
    assert "naive" in detail.lower() or "timezone" in detail.lower()

    # With timezone offset, it succeeds
    aware_dt = f"{test_day}T10:00:00+00:00"
    resp_ok = await client.post(
        "/admin/tables/intake_reports/rows",
        json={
            "values": {
                "user_id": str(user_id),
                "period": "MORNING",
                "date": str(test_day),
                "taken_at": aware_dt,
                "snapshot": [],
            }
        },
    )
    assert resp_ok.status_code == 201


@pytest.mark.asyncio
async def test_delete_user_cascades_as_defined_by_db(client_factory, make_user):
    admin_id = await make_user("admin@example.com")
    target_user_id = await make_user("target@example.com")
    client = client_factory(admin_id)

    # Create a measurement referencing target_user_id
    meas_resp = await client.post(
        "/admin/tables/measurements/rows",
        json={"values": {"user_id": str(target_user_id), "sys": 120, "dia": 80, "pulse": 70}},
    )
    assert meas_resp.status_code == 201
    meas_id = meas_resp.json()["id"]

    # Delete the user via admin API
    del_resp = await client.post(
        "/admin/tables/users/rows/delete",
        json={"pk": {"id": str(target_user_id)}},
    )
    # The database schema specifies ON DELETE CASCADE on measurements.user_id -> users.id
    assert del_resp.status_code == 204

    # Verify that the measurement was cascaded and is now gone
    list_resp = await client.get(f"/admin/tables/measurements/rows?filter=id:{meas_id}")
    assert list_resp.status_code == 200
    assert list_resp.json()["total"] == 0


@pytest.mark.asyncio
async def test_non_admin_gets_403_on_every_route(client_factory, make_user):
    regular_user_id = await make_user("testuser@example.com")
    client = client_factory(regular_user_id)

    # Every admin route must return 403 for regular user
    r1 = await client.get("/admin/tables")
    assert r1.status_code == 403
    assert r1.json()["detail"] == "Admin only"

    r2 = await client.get("/admin/tables/measurements/rows")
    assert r2.status_code == 403
    assert r2.json()["detail"] == "Admin only"

    r3 = await client.post(
        "/admin/tables/measurements/rows",
        json={"values": {"sys": 120}},
    )
    assert r3.status_code == 403
    assert r3.json()["detail"] == "Admin only"

    r4 = await client.patch(
        "/admin/tables/measurements/rows",
        json={"pk": {"id": str(uuid.uuid4())}, "values": {"sys": 120}},
    )
    assert r4.status_code == 403
    assert r4.json()["detail"] == "Admin only"

    r5 = await client.post(
        "/admin/tables/measurements/rows/delete",
        json={"pk": {"id": str(uuid.uuid4())}},
    )
    assert r5.status_code == 403
    assert r5.json()["detail"] == "Admin only"


@pytest.mark.asyncio
async def test_unauthenticated_gets_401():
    app.dependency_overrides.clear()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.get("/admin/tables")
        assert r1.status_code == 401

        r2 = await client.get("/admin/tables/measurements/rows")
        assert r2.status_code == 401

        r3 = await client.post("/admin/tables/measurements/rows", json={"values": {}})
        assert r3.status_code == 401

        r4 = await client.patch("/admin/tables/measurements/rows", json={"pk": {}, "values": {}})
        assert r4.status_code == 401

        r5 = await client.post("/admin/tables/measurements/rows/delete", json={"pk": {}})
        assert r5.status_code == 401


@pytest.mark.asyncio
async def test_error_status_codes(client_factory, make_user):
    admin_id = await make_user("admin@example.com")
    client = client_factory(admin_id)

    # 1. Unknown table: 404
    resp = await client.get("/admin/tables/nonexistent_table/rows")
    assert resp.status_code == 404

    # 2. Forbidden operations: 405
    resp = await client.post("/admin/tables/email_outbox/rows", json={"values": {}})
    assert resp.status_code == 405

    resp = await client.patch(
        "/admin/tables/email_outbox/rows", json={"pk": {"id": str(uuid.uuid4())}, "values": {}}
    )
    assert resp.status_code == 405

    resp = await client.post(
        "/admin/tables/email_outbox/rows/delete", json={"pk": {"id": str(uuid.uuid4())}}
    )
    assert resp.status_code == 405

    # 3. Row not found: 404
    resp = await client.patch(
        "/admin/tables/measurements/rows",
        json={"pk": {"id": str(uuid.uuid4())}, "values": {"sys": 130}},
    )
    assert resp.status_code == 404

    resp = await client.post(
        "/admin/tables/measurements/rows/delete",
        json={"pk": {"id": str(uuid.uuid4())}},
    )
    assert resp.status_code == 404

    # 4. Conflict error: 409
    # Foreign key violation: measurement with non-existent user_id
    resp = await client.post(
        "/admin/tables/measurements/rows",
        json={"values": {"user_id": str(uuid.uuid4()), "sys": 120, "dia": 80, "pulse": 70}},
    )
    assert resp.status_code == 409
    err_detail = resp.json()["detail"].lower()
    assert "foreign key" in err_detail or "violates" in err_detail
