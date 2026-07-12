import base64
import csv
from datetime import UTC, datetime

import pytest
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from auth.models import User
from config import get_settings
from email_infra.models import EmailOutbox, EmailStatus
from measurements.models import Measurement


@pytest.mark.asyncio
async def test_export_happy_path(
    session: AsyncSession,
    client_factory,
    make_user,
):
    # 1. Happy path: user with three measurements -> 202, queues one PENDING outbox item
    user_id = await make_user("happy@example.com")
    client = client_factory(user_id)

    # Insert 3 measurements out of order chronologically
    m1 = Measurement(
        sys=120,
        dia=80,
        pulse=70,
        recorded_at=datetime(2026, 7, 10, 10, 0, 0, tzinfo=UTC),
        user_id=user_id,
    )
    m2 = Measurement(
        sys=130,
        dia=85,
        pulse=75,
        recorded_at=datetime(2026, 7, 11, 11, 0, 0, tzinfo=UTC),
        user_id=user_id,
    )
    m3 = Measurement(
        sys=115,
        dia=75,
        pulse=65,
        recorded_at=datetime(2026, 7, 9, 9, 0, 0, tzinfo=UTC),
        user_id=user_id,
    )
    session.add_all([m1, m2, m3])
    await session.commit()

    response = await client.post("/export/csv")
    assert response.status_code == 202
    data = response.json()
    assert data["message"] == "Export is queued"
    assert data["email"] == "happy@example.com"

    # Check last_export_at was updated on the user
    user = await session.get(User, user_id)
    assert user.last_export_at is not None
    assert abs((datetime.now(UTC) - user.last_export_at).total_seconds()) < 10

    # Query outbox
    statement = select(EmailOutbox).where(EmailOutbox.to == "happy@example.com")
    result = await session.exec(statement)
    outbox_items = list(result.all())
    assert len(outbox_items) == 1

    item = outbox_items[0]
    assert item.status == EmailStatus.PENDING
    assert item.subject.startswith("BP Tracker — export from")

    assert item.attachments is not None
    assert len(item.attachments) == 1
    att = item.attachments[0]
    assert att["filename"].startswith("bp-tracker-")
    assert att["filename"].endswith(".csv")
    assert att["content_type"] == "text/csv"

    # Decode CSV content
    csv_bytes = base64.b64decode(att["content_b64"])
    csv_text = csv_bytes.decode("utf-8")
    rows = list(csv.reader(csv_text.splitlines()))

    assert rows[0] == ["timestamp", "systolic", "diastolic", "pulse"]
    # Check they are sorted ascending by recorded_at
    assert rows[1] == ["2026-07-09 09:00:00", "115", "75", "65"]
    assert rows[2] == ["2026-07-10 10:00:00", "120", "80", "70"]
    assert rows[3] == ["2026-07-11 11:00:00", "130", "85", "75"]


@pytest.mark.asyncio
async def test_export_cooldown(
    session: AsyncSession,
    client_factory,
    make_user,
):
    # 2. Cooldown check: second request yields 429 and no new outbox item
    user_id = await make_user("cooldown@example.com")
    client = client_factory(user_id)

    # First call succeeds
    response1 = await client.post("/export/csv")
    assert response1.status_code == 202

    # Second call returns 429
    response2 = await client.post("/export/csv")
    assert response2.status_code == 429
    assert response2.json()["detail"] == "Export already requested recently"

    # Verify only one outbox row exists
    statement = select(EmailOutbox).where(EmailOutbox.to == "cooldown@example.com")
    result = await session.exec(statement)
    assert len(list(result.all())) == 1


@pytest.mark.asyncio
async def test_export_data_isolation(
    session: AsyncSession,
    client_factory,
    make_user,
):
    # 3. Isolation: export contains only the calling user's measurements
    user_a = await make_user("user_a@example.com")
    user_b = await make_user("user_b@example.com")

    # Measurements for both users
    m_a = Measurement(
        sys=120,
        dia=80,
        pulse=70,
        recorded_at=datetime(2026, 7, 10, 10, 0, 0, tzinfo=UTC),
        user_id=user_a,
    )
    m_b = Measurement(
        sys=140,
        dia=90,
        pulse=80,
        recorded_at=datetime(2026, 7, 10, 10, 0, 0, tzinfo=UTC),
        user_id=user_b,
    )
    session.add_all([m_a, m_b])
    await session.commit()

    client = client_factory(user_a)
    response = await client.post("/export/csv")
    assert response.status_code == 202

    statement = select(EmailOutbox).where(EmailOutbox.to == "user_a@example.com")
    result = await session.exec(statement)
    item = result.first()

    csv_bytes = base64.b64decode(item.attachments[0]["content_b64"])
    csv_text = csv_bytes.decode("utf-8")
    rows = list(csv.reader(csv_text.splitlines()))

    assert len(rows) == 2  # header + 1 data row
    assert rows[1] == ["2026-07-10 10:00:00", "120", "80", "70"]


@pytest.mark.asyncio
async def test_export_timezone(
    session: AsyncSession,
    client_factory,
    make_user,
):
    # 4. Timezone shifting: Europe/Kyiv (+3 in July) vs NULL (UTC)
    # Kyiv user
    user_kyiv_id = await make_user("kyiv@example.com")
    user_kyiv = await session.get(User, user_kyiv_id)
    user_kyiv.timezone = "Europe/Kyiv"
    session.add(user_kyiv)

    m_kyiv = Measurement(
        sys=120,
        dia=80,
        pulse=70,
        recorded_at=datetime(2026, 7, 12, 12, 0, 0, tzinfo=UTC),
        user_id=user_kyiv_id,
    )
    session.add(m_kyiv)
    await session.commit()

    client_kyiv = client_factory(user_kyiv_id)
    await client_kyiv.post("/export/csv")

    statement_kyiv = select(EmailOutbox).where(EmailOutbox.to == "kyiv@example.com")
    result_kyiv = await session.exec(statement_kyiv)
    item_kyiv = result_kyiv.first()
    csv_kyiv = base64.b64decode(item_kyiv.attachments[0]["content_b64"]).decode("utf-8")
    rows_kyiv = list(csv.reader(csv_kyiv.splitlines()))
    # Kyiv is UTC+3 in July, so 12:00 UTC -> 15:00 Kyiv
    assert rows_kyiv[1][0] == "2026-07-12 15:00:00"

    # UTC user (NULL timezone)
    user_utc_id = await make_user("utc@example.com")
    m_utc = Measurement(
        sys=120,
        dia=80,
        pulse=70,
        recorded_at=datetime(2026, 7, 12, 12, 0, 0, tzinfo=UTC),
        user_id=user_utc_id,
    )
    session.add(m_utc)
    await session.commit()

    client_utc = client_factory(user_utc_id)
    await client_utc.post("/export/csv")

    statement_utc = select(EmailOutbox).where(EmailOutbox.to == "utc@example.com")
    result_utc = await session.exec(statement_utc)
    item_utc = result_utc.first()
    csv_utc = base64.b64decode(item_utc.attachments[0]["content_b64"]).decode("utf-8")
    rows_utc = list(csv.reader(csv_utc.splitlines()))
    # No timezone shifts, defaults to UTC
    assert rows_utc[1][0] == "2026-07-12 12:00:00"


@pytest.mark.asyncio
async def test_export_empty_history(
    session: AsyncSession,
    client_factory,
    make_user,
):
    # 5. Empty history: still returns 202, queues outbox with only headers
    user_id = await make_user("empty@example.com")
    client = client_factory(user_id)

    response = await client.post("/export/csv")
    assert response.status_code == 202

    statement = select(EmailOutbox).where(EmailOutbox.to == "empty@example.com")
    result = await session.exec(statement)
    item = result.first()
    assert item is not None

    csv_bytes = base64.b64decode(item.attachments[0]["content_b64"])
    csv_text = csv_bytes.decode("utf-8")
    rows = list(csv.reader(csv_text.splitlines()))

    assert len(rows) == 1
    assert rows[0] == ["timestamp", "systolic", "diastolic", "pulse"]


@pytest.mark.asyncio
async def test_export_body_content(
    session: AsyncSession,
    client_factory,
    make_user,
):
    # 6. Body content contains the EXPORT_SHEETS_TEMPLATE_URL link
    user_id = await make_user("body@example.com")
    client = client_factory(user_id)

    await client.post("/export/csv")

    statement = select(EmailOutbox).where(EmailOutbox.to == "body@example.com")
    result = await session.exec(statement)
    item = result.first()

    settings = get_settings()
    assert settings.export_sheets_template_url in item.body
