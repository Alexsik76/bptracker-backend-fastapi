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

    response = await client.post("/export/csv", json={"tz": "UTC"})
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
    assert len(item.attachments) == 2

    # Attachment 1: CSV
    att_csv = item.attachments[0]
    assert att_csv["filename"].startswith("bp-tracker-")
    assert att_csv["filename"].endswith(".csv")
    assert att_csv["content_type"] == "text/csv"

    # Attachment 2: PDF
    att_pdf = item.attachments[1]
    assert att_pdf["filename"].startswith("bp-tracker-")
    assert att_pdf["filename"].endswith(".pdf")
    assert att_pdf["content_type"] == "application/pdf"
    pdf_bytes = base64.b64decode(att_pdf["content_b64"])
    assert pdf_bytes.startswith(b"%PDF")

    # Decode CSV content
    csv_bytes = base64.b64decode(att_csv["content_b64"])
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
    response1 = await client.post("/export/csv", json={"tz": "UTC"})
    assert response1.status_code == 202

    # Second call returns 429
    response2 = await client.post("/export/csv", json={"tz": "UTC"})
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
    response = await client.post("/export/csv", json={"tz": "UTC"})
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
    # 4. Timezone shifting: Europe/Kyiv (+3 in July) vs UTC
    user_kyiv_id = await make_user("kyiv@example.com")

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
    await client_kyiv.post("/export/csv", json={"tz": "Europe/Kyiv"})

    statement_kyiv = select(EmailOutbox).where(EmailOutbox.to == "kyiv@example.com")
    result_kyiv = await session.exec(statement_kyiv)
    item_kyiv = result_kyiv.first()
    csv_kyiv = base64.b64decode(item_kyiv.attachments[0]["content_b64"]).decode("utf-8")
    rows_kyiv = list(csv.reader(csv_kyiv.splitlines()))
    # Kyiv is UTC+3 in July, so 12:00 UTC -> 15:00 Kyiv
    assert rows_kyiv[1][0] == "2026-07-12 15:00:00"


@pytest.mark.asyncio
async def test_export_timezone_filename_date(
    session: AsyncSession,
    client_factory,
    make_user,
):
    from unittest.mock import patch

    user_id = await make_user("tokyo@example.com")
    client = client_factory(user_id)

    class FakeDatetime:
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 7, 12, 22, 30, 0, tzinfo=UTC)

    with patch("export.service.datetime", FakeDatetime):
        response = await client.post("/export/csv", json={"tz": "Asia/Tokyo"})
        assert response.status_code == 202

    statement = select(EmailOutbox).where(EmailOutbox.to == "tokyo@example.com")
    result = await session.exec(statement)
    item = result.first()
    assert item is not None

    # Tokyo is UTC+9. 22:30 UTC -> 07:30 Tokyo of the next day (July 13th)
    assert "2026-07-13" in item.subject
    assert "2026-07-13" in item.attachments[0]["filename"]


@pytest.mark.asyncio
async def test_export_missing_tz(
    client_factory,
    make_user,
):
    user_id = await make_user("missing-tz@example.com")
    client = client_factory(user_id)

    # Missing tz yields 422
    response = await client.post("/export/csv")
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_export_invalid_tz(
    client_factory,
    make_user,
):
    user_id = await make_user("invalid-tz@example.com")
    client = client_factory(user_id)

    # Invalid tz yields 422
    response = await client.post("/export/csv", json={"tz": "Not/AZone"})
    assert response.status_code == 422
    assert "Invalid timezone identifier" in response.json()["detail"][0]["msg"]


@pytest.mark.asyncio
async def test_export_empty_history(
    session: AsyncSession,
    client_factory,
    make_user,
):
    # 5. Empty history: still returns 202, queues outbox with only headers
    user_id = await make_user("empty@example.com")
    client = client_factory(user_id)

    response = await client.post("/export/csv", json={"tz": "UTC"})
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

    await client.post("/export/csv", json={"tz": "UTC"})

    statement = select(EmailOutbox).where(EmailOutbox.to == "body@example.com")
    result = await session.exec(statement)
    item = result.first()

    settings = get_settings()
    assert settings.export_sheets_template_url in item.body


@pytest.mark.asyncio
async def test_export_user_not_found(
    client_factory,
):
    from uuid import uuid4

    from auth.deps import get_current_user_id
    from main import app

    app.dependency_overrides.clear()
    dummy_id = uuid4()
    app.dependency_overrides[get_current_user_id] = lambda: dummy_id

    client = client_factory(dummy_id)
    response = await client.post("/export/csv", json={"tz": "UTC"})
    assert response.status_code == 404
    assert response.json()["detail"] == "User not found"


def test_generate_measurements_pdf_with_data():
    import zoneinfo
    from uuid import uuid4

    from export.service import generate_measurements_pdf

    user_id = uuid4()
    tz = zoneinfo.ZoneInfo("UTC")
    m1 = Measurement(
        sys=120,
        dia=80,
        pulse=70,
        recorded_at=datetime(2026, 4, 17, 8, 30, 0, tzinfo=UTC),
        user_id=user_id,
    )
    m2 = Measurement(
        sys=130,
        dia=85,
        pulse=75,
        recorded_at=datetime(2026, 4, 23, 19, 45, 0, tzinfo=UTC),
        user_id=user_id,
    )

    pdf_bytes = generate_measurements_pdf(
        [m1, m2],
        tz,
        patient_label="Олена Коваль",
        generated_at=datetime(2026, 4, 23, 20, 0, 0, tzinfo=UTC),
    )

    assert isinstance(pdf_bytes, bytes)
    assert len(pdf_bytes) > 0
    assert pdf_bytes.startswith(b"%PDF")


def test_generate_measurements_pdf_empty():
    import zoneinfo

    from export.service import generate_measurements_pdf

    tz = zoneinfo.ZoneInfo("Europe/Kyiv")
    pdf_bytes = generate_measurements_pdf(
        [],
        tz,
        patient_label="test@example.com",
        generated_at=datetime(2026, 7, 21, 10, 0, 0, tzinfo=UTC),
    )

    assert isinstance(pdf_bytes, bytes)
    assert len(pdf_bytes) > 0
    assert pdf_bytes.startswith(b"%PDF")


@pytest.mark.asyncio
async def test_export_with_display_name(
    session: AsyncSession,
    client_factory,
    make_user,
):
    user_id = await make_user("named@example.com")
    user = await session.get(User, user_id)
    user.display_name = "Тарас Шевченко"
    session.add(user)
    await session.commit()

    client = client_factory(user_id)
    response = await client.post("/export/csv", json={"tz": "UTC"})
    assert response.status_code == 202

    statement = select(EmailOutbox).where(EmailOutbox.to == "named@example.com")
    result = await session.exec(statement)
    item = result.first()
    assert item is not None
    assert len(item.attachments) == 2
    pdf_att = item.attachments[1]
    assert pdf_att["content_type"] == "application/pdf"
    pdf_bytes = base64.b64decode(pdf_att["content_b64"])
    assert pdf_bytes.startswith(b"%PDF")


@pytest.mark.asyncio
async def test_export_date_range_filtering(
    session: AsyncSession,
    client_factory,
    make_user,
):
    user_id = await make_user("range@example.com")

    # Kyiv is UTC+3 in July
    # m1: July 9th 20:30 UTC -> July 9th 23:30 Kyiv (BEFORE range)
    m1 = Measurement(
        sys=110,
        dia=70,
        pulse=60,
        recorded_at=datetime(2026, 7, 9, 20, 30, 0, tzinfo=UTC),
        user_id=user_id,
    )
    # m2: July 9th 21:30 UTC -> July 10th 00:30 Kyiv (IN range for 2026-07-10)
    m2 = Measurement(
        sys=120,
        dia=80,
        pulse=70,
        recorded_at=datetime(2026, 7, 9, 21, 30, 0, tzinfo=UTC),
        user_id=user_id,
    )
    # m3: July 10th 12:00 UTC -> July 10th 15:00 Kyiv (IN range for 2026-07-10)
    m3 = Measurement(
        sys=130,
        dia=85,
        pulse=75,
        recorded_at=datetime(2026, 7, 10, 12, 0, 0, tzinfo=UTC),
        user_id=user_id,
    )
    # m4: July 10th 21:30 UTC -> July 11th 00:30 Kyiv (AFTER range for 2026-07-10)
    m4 = Measurement(
        sys=140,
        dia=90,
        pulse=80,
        recorded_at=datetime(2026, 7, 10, 21, 30, 0, tzinfo=UTC),
        user_id=user_id,
    )
    session.add_all([m1, m2, m3, m4])
    await session.commit()

    client = client_factory(user_id)
    response = await client.post(
        "/export/csv",
        json={
            "tz": "Europe/Kyiv",
            "date_from": "2026-07-10",
            "date_to": "2026-07-10",
        },
    )
    assert response.status_code == 202

    statement = select(EmailOutbox).where(EmailOutbox.to == "range@example.com")
    result = await session.exec(statement)
    item = result.first()
    assert item is not None
    assert len(item.attachments) == 2

    csv_bytes = base64.b64decode(item.attachments[0]["content_b64"])
    rows = list(csv.reader(csv_bytes.decode("utf-8").splitlines()))

    # Header + 2 in-range measurements (m2 and m3)
    assert len(rows) == 3
    assert rows[1] == ["2026-07-10 00:30:00", "120", "80", "70"]
    assert rows[2] == ["2026-07-10 15:00:00", "130", "85", "75"]


@pytest.mark.asyncio
async def test_export_invalid_date_range(
    client_factory,
    make_user,
):
    user_id = await make_user("invalid_range@example.com")
    client = client_factory(user_id)

    response = await client.post(
        "/export/csv",
        json={
            "tz": "UTC",
            "date_from": "2026-07-15",
            "date_to": "2026-07-10",
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_export_empty_result_date_range(
    session: AsyncSession,
    client_factory,
    make_user,
):
    user_id = await make_user("empty_range@example.com")
    m = Measurement(
        sys=120,
        dia=80,
        pulse=70,
        recorded_at=datetime(2026, 7, 10, 10, 0, 0, tzinfo=UTC),
        user_id=user_id,
    )
    session.add(m)
    await session.commit()

    client = client_factory(user_id)
    response = await client.post(
        "/export/csv",
        json={
            "tz": "UTC",
            "date_from": "2026-08-01",
            "date_to": "2026-08-05",
        },
    )
    assert response.status_code == 202

    statement = select(EmailOutbox).where(EmailOutbox.to == "empty_range@example.com")
    result = await session.exec(statement)
    item = result.first()
    assert item is not None
    assert len(item.attachments) == 2

    # CSV has header only
    csv_bytes = base64.b64decode(item.attachments[0]["content_b64"])
    rows = list(csv.reader(csv_bytes.decode("utf-8").splitlines()))
    assert len(rows) == 1

    # PDF is valid empty report
    pdf_bytes = base64.b64decode(item.attachments[1]["content_b64"])
    assert pdf_bytes.startswith(b"%PDF")
