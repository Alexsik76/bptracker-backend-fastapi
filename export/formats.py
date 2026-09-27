import csv
import io
import zoneinfo
from datetime import datetime
from pathlib import Path

from fpdf import FPDF
from fpdf.fonts import FontFace

from measurements.models import Measurement

FONTS_DIR = Path(__file__).parent / "fonts"
DEJAVU_SANS = FONTS_DIR / "DejaVuSans.ttf"
DEJAVU_SANS_BOLD = FONTS_DIR / "DejaVuSans-Bold.ttf"


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
