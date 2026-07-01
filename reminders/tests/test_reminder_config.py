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


@pytest.mark.asyncio
async def test_get_reminder_config_returns_404_when_not_configured(client):
    response = await client.get("/reminders/config")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_upsert_creates_reminder_config(client, config_payload):
    response = await client.put("/reminders/config", json=config_payload)
    assert response.status_code == 200
    body = response.json()

    assert body["morning_time"] == "08:00:00"
    assert body["max_reminders"] == 3
    assert "user_id" not in body

    fetched = await client.get("/reminders/config")
    assert fetched.status_code == 200
    assert fetched.json()["duration_minutes"] == 60


@pytest.mark.asyncio
async def test_upsert_overwrites_existing_config(client, config_payload):
    await client.put("/reminders/config", json=config_payload)

    updated_payload = {**config_payload, "max_reminders": 5}
    response = await client.put("/reminders/config", json=updated_payload)
    assert response.status_code == 200
    assert response.json()["max_reminders"] == 5

    fetched = await client.get("/reminders/config")
    assert fetched.json()["max_reminders"] == 5


@pytest.mark.asyncio
async def test_upsert_rejects_missing_required_field(client, config_payload):
    del config_payload["duration_minutes"]
    response = await client.put("/reminders/config", json=config_payload)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_user_cannot_see_another_users_reminder_config(
    client_factory, make_user, config_payload
):
    user_a = await make_user("a@example.com")
    user_b = await make_user("b@example.com")

    client_a = client_factory(user_a)
    created = await client_a.put("/reminders/config", json=config_payload)
    assert created.status_code == 200

    client_b = client_factory(user_b)
    response = await client_b.get("/reminders/config")
    assert response.status_code == 404
