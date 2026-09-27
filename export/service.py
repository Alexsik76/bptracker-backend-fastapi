import zoneinfo
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID

from sqlmodel.ext.asyncio.session import AsyncSession

from config import Settings
from email_infra import EmailAttachment, OutboxEmailSender
from export import crud as export_crud
from export.formats import generate_measurements_csv, generate_measurements_pdf
from measurements import crud as measurements_crud
from users import crud as users_crud


class UserNotFound(Exception):
    pass


class ExportCooldownActive(Exception):
    pass


async def export_measurements_to_csv(
    session: AsyncSession,
    *,
    user_id: UUID,
    settings: Settings,
    tz: str,
    date_from: date | None = None,
    date_to: date | None = None,
) -> str:
    """Orchestrates the export process:

    1. Fetches the user (plain read, no lock).
    2. Locks and checks cooldown via export_state (locks user if no state exists).
    3. Fetches all measurements for the user (filtered by date range if provided).
    4. Generates the CSV and PDF bytes formatted to the target timezone.
    5. Queues the email using OutboxEmailSender in the same transaction.
    6. Stamps export_state with the current timestamp.
    7. Commits the transaction atomically.
    """
    user = await users_crud.get_user(session, user_id)
    if not user:
        raise UserNotFound()

    export_state = await export_crud.lock_export_state(session, user_id)
    if export_state is None:
        # When no export_state exists yet, lock the user row to serialize concurrent first exports
        await users_crud.lock_user(session, user_id)
        export_state = await export_crud.lock_export_state(session, user_id)

    now_utc = datetime.now(UTC)
    if export_state is not None and export_state.last_export_at is not None:
        cooldown_limit = export_state.last_export_at + timedelta(
            minutes=settings.export_cooldown_minutes
        )
        if now_utc < cooldown_limit:
            raise ExportCooldownActive()

    tz_info = zoneinfo.ZoneInfo(tz)

    date_from_utc = (
        datetime.combine(date_from, time.min, tzinfo=tz_info).astimezone(UTC)
        if date_from is not None
        else None
    )
    date_to_utc = (
        datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=tz_info).astimezone(UTC)
        if date_to is not None
        else None
    )

    measurements = list(
        await measurements_crud.get_measurements_for_export(
            session,
            user_id,
            date_from=date_from_utc,
            date_to=date_to_utc,
        )
    )

    # Build CSV and PDF attachments
    csv_bytes = generate_measurements_csv(measurements, tz_info)

    patient_label = user.display_name or user.email
    pdf_bytes = generate_measurements_pdf(
        measurements,
        tz_info,
        patient_label=patient_label,
        generated_at=now_utc,
    )

    now_local = now_utc.astimezone(tz_info)
    today_str = now_local.strftime("%Y-%m-%d")
    csv_filename = f"bp-tracker-{today_str}.csv"
    pdf_filename = f"bp-tracker-{today_str}.pdf"
    subject = f"BP Tracker — export from {today_str}"

    csv_attachment = EmailAttachment(
        filename=csv_filename,
        content=csv_bytes,
        content_type="text/csv",
    )
    pdf_attachment = EmailAttachment(
        filename=pdf_filename,
        content=pdf_bytes,
        content_type="application/pdf",
    )

    # Queue email in the outbox
    email_sender = OutboxEmailSender(session)
    body_text = (
        "Your BP Tracker data is attached:\n"
        "- CSV — raw measurement data;\n"
        "- PDF — print-ready report for your doctor.\n\n"
        "To view the data as a dashboard, copy the Google Sheets template\n"
        f"({settings.export_sheets_template_url}) and import the CSV via\n"
        "File → Import → Replace current sheet."
    )

    await email_sender.send(
        to=user.email,
        subject=subject,
        text=body_text,
        attachments=[csv_attachment, pdf_attachment],
    )

    # Update export state timestamp
    await export_crud.stamp_export(session, user_id, now_utc)

    # Atomic commit of both outbox insert and export state update
    await session.commit()

    return user.email
