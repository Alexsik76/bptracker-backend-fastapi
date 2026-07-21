import csv
import io
import zoneinfo
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from fpdf import FPDF
from fpdf.fonts import FontFace
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from auth.models import User
from config import Settings
from email_infra import EmailAttachment, OutboxEmailSender
from measurements.models import Measurement

FONTS_DIR = Path(__file__).parent / "fonts"
DEJAVU_SANS = FONTS_DIR / "DejaVuSans.ttf"
DEJAVU_SANS_BOLD = FONTS_DIR / "DejaVuSans-Bold.ttf"


class UserNotFound(Exception):
    pass


class ExportCooldownActive(Exception):
    pass


class BPReportPDF(FPDF):
    def __init__(self, generated_at_str: str) -> None:
        super().__init__(orientation="P", unit="mm", format="A4")
        self.generated_at_str = generated_at_str
        self.add_font("DejaVu", "", str(DEJAVU_SANS))
        self.add_font("DejaVu", "B", str(DEJAVU_SANS_BOLD))
        self.alias_nb_pages()

    def footer(self) -> None:
        self.set_y(-15)
        self.set_font("DejaVu", "", 9)
        self.set_text_color(100, 100, 100)
        left_text = f"Сформовано: {self.generated_at_str}"
        right_text = f"Сторінка {self.page_no()} з {{nb}}"
        self.cell(0, 10, left_text, align="L")
        self.set_x(self.l_margin)
        self.cell(0, 10, right_text, align="R")


def generate_measurements_pdf(
    measurements: list[Measurement],
    tz: zoneinfo.ZoneInfo,
    *,
    patient_label: str,
    generated_at: datetime,
) -> bytes:
    gen_at_local = generated_at.astimezone(tz)
    gen_at_str = gen_at_local.strftime("%d.%m.%Y %H:%M")

    pdf = BPReportPDF(gen_at_str)
    pdf.add_page()

    # Title
    pdf.set_font("DejaVu", "B", 16)
    pdf.cell(0, 10, "Журнал артеріального тиску", new_x="LMARGIN", new_y="NEXT")

    # Subtitles
    pdf.set_font("DejaVu", "", 11)
    pdf.cell(0, 6, f"Пацієнт: {patient_label}", new_x="LMARGIN", new_y="NEXT")

    if measurements:
        start_date = measurements[0].recorded_at.astimezone(tz).strftime("%d.%m.%Y")
        end_date = measurements[-1].recorded_at.astimezone(tz).strftime("%d.%m.%Y")
        period_str = f"Період: {start_date} — {end_date}"
    else:
        period_str = "Період: —"

    pdf.cell(0, 6, period_str, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    # Table headers and data
    headers = ["Дата", "Час", "САТ, мм рт. ст.", "ДАТ, мм рт. ст.", "Пульс, уд/хв"]
    headings_style = FontFace(
        family="DejaVu",
        emphasis="B",
        size_pt=10,
        fill_color=(220, 220, 220),
    )

    with pdf.table(
        headings_style=headings_style,
        col_widths=(25, 20, 40, 40, 35),
        text_align=("CENTER", "CENTER", "CENTER", "CENTER", "CENTER"),
        line_height=7,
        cell_fill_color=(245, 245, 245),
        cell_fill_mode="ROWS",
    ) as table:
        header_row = table.row()
        for h in headers:
            header_row.cell(h)

        pdf.set_font("DejaVu", "", 10)
        for m in measurements:
            local_dt = m.recorded_at.astimezone(tz)
            date_str = local_dt.strftime("%d.%m.%Y")
            time_str = local_dt.strftime("%H:%M")
            r = table.row()
            r.cell(date_str)
            r.cell(time_str)
            r.cell(str(m.sys))
            r.cell(str(m.dia))
            r.cell(str(m.pulse))

    return bytes(pdf.output())


def generate_measurements_csv(measurements: list[Measurement], tz: zoneinfo.ZoneInfo) -> bytes:
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["timestamp", "systolic", "diastolic", "pulse"])

    for m in measurements:
        # Convert the timestamp to the target local timezone supplied by the client.
        # This renders the file server-side using the request-time timezone, ensuring
        # the dates and times align with the client's current context.
        local_dt = m.recorded_at.astimezone(tz)
        timestamp_str = local_dt.strftime("%Y-%m-%d %H:%M:%S")
        writer.writerow([timestamp_str, m.sys, m.dia, m.pulse])

    return output.getvalue().encode("utf-8")


async def export_measurements_to_csv(
    session: AsyncSession,
    *,
    user_id: UUID,
    settings: Settings,
    tz: str,
) -> str:
    """Orchestrates the export process:

    1. Fetches the user (with with_for_update() to prevent race conditions).
    2. Performs cooldown validation.
    3. Fetches all measurements for the user in ascending date order.
    4. Generates the CSV and PDF bytes formatted to the target timezone.
    5. Queues the email using OutboxEmailSender in the same transaction.
    6. Stamping user.last_export_at.
    7. Commits the transaction atomically.
    """
    # Fetch user with write lock to prevent concurrent export cooldown bypasses
    statement = select(User).where(User.id == user_id).with_for_update()
    result = await session.exec(statement)
    user = result.first()
    if not user:
        raise UserNotFound()

    now_utc = datetime.now(UTC)
    if user.last_export_at is not None:
        cooldown_limit = user.last_export_at + timedelta(minutes=settings.export_cooldown_minutes)
        if now_utc < cooldown_limit:
            raise ExportCooldownActive()

    # Fetch all measurements ordered ascending
    m_statement = (
        select(Measurement)
        .where(Measurement.user_id == user_id)
        .order_by(Measurement.recorded_at.asc())
    )
    m_result = await session.exec(m_statement)
    measurements = list(m_result.all())

    # Build CSV and PDF attachments
    tz_info = zoneinfo.ZoneInfo(tz)
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

    # Update last_export_at timestamp
    user.last_export_at = now_utc
    session.add(user)

    # Atomic commit of both outbox insert and user timestamp update
    await session.commit()

    return user.email
