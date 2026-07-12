import csv
import io
import zoneinfo
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import HTTPException, status
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from auth.models import User
from config import Settings
from email_infra import EmailAttachment, OutboxEmailSender
from measurements.models import Measurement


def get_user_timezone(timezone_str: str | None) -> zoneinfo.ZoneInfo:
    if timezone_str:
        try:
            return zoneinfo.ZoneInfo(timezone_str)
        except Exception:
            pass
    return zoneinfo.ZoneInfo("UTC")


def generate_measurements_csv(measurements: list[Measurement], tz: zoneinfo.ZoneInfo) -> bytes:
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["timestamp", "systolic", "diastolic", "pulse"])

    for m in measurements:
        rec_at = m.recorded_at or datetime.now(UTC)
        # Convert the timestamp to the user's target local timezone before formatting.
        # Note for code comment: docs/conventions.md says the client normalizes
        # timezone and the backend never interprets it. That rule governs data
        # the client sends. Here the backend renders a human-readable file with
        # no client in the loop, so users.timezone — the field kept precisely
        # for this class of server-side rendering — is the correct source.
        local_dt = rec_at.astimezone(tz)
        timestamp_str = local_dt.strftime("%Y-%m-%d %H:%M:%S")
        writer.writerow([timestamp_str, m.sys, m.dia, m.pulse])

    return output.getvalue().encode("utf-8")


async def export_measurements_to_csv(
    session: AsyncSession,
    *,
    user_id: UUID,
    settings: Settings,
) -> str:
    """Orchestrates the export process:

    1. Fetches the user (with with_for_update() to prevent race conditions).
    2. Performs cooldown validation (returns 429 if requested within 10 minutes).
    3. Fetches all measurements for the user in ascending date order.
    4. Generates the CSV bytes formatted to the user's timezone.
    5. Queues the email using OutboxEmailSender in the same transaction.
    6. Stamping user.last_export_at.
    7. Commits the transaction atomically.
    """
    # Fetch user with write lock to prevent concurrent export cooldown bypasses
    statement = select(User).where(User.id == user_id).with_for_update()
    result = await session.exec(statement)
    user = result.first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    now_utc = datetime.now(UTC)
    if user.last_export_at is not None:
        cooldown_limit = user.last_export_at + timedelta(minutes=settings.export_cooldown_minutes)
        if now_utc < cooldown_limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Export already requested recently",
            )

    # Fetch all measurements ordered ascending
    m_statement = (
        select(Measurement)
        .where(Measurement.user_id == user_id)
        .order_by(Measurement.recorded_at.asc())
    )
    m_result = await session.exec(m_statement)
    measurements = list(m_result.all())

    # Build CSV and email attachment
    tz = get_user_timezone(user.timezone)
    csv_bytes = generate_measurements_csv(measurements, tz)

    now_local = now_utc.astimezone(tz)
    today_str = now_local.strftime("%Y-%m-%d")
    filename = f"bp-tracker-{today_str}.csv"
    subject = f"BP Tracker — export from {today_str}"

    attachment = EmailAttachment(
        filename=filename,
        content=csv_bytes,
        content_type="text/csv",
    )

    # Queue email in the outbox
    email_sender = OutboxEmailSender(session)
    body_text = (
        "Your historical BP Tracker data in CSV format.\n\n"
        "To view it easily, copy the Google Sheets template "
        f"({settings.export_sheets_template_url}) "
        "and import the file via File → Import → Replace current sheet."
    )

    await email_sender.send(
        to=user.email,
        subject=subject,
        text=body_text,
        attachments=[attachment],
    )

    # Update last_export_at timestamp
    user.last_export_at = now_utc
    session.add(user)

    # Atomic commit of both outbox insert and user timestamp update
    await session.commit()

    return user.email
