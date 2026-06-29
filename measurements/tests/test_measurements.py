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
