from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from prescriptions.models import MedicationItem, Prescription, WhenSlot
from reminders.models import IntakeReport, IntakeReportCreate


async def _build_snapshot(
    session: AsyncSession,
    user_id: UUID,
    period: WhenSlot,
) -> list[dict]:
    """Collect {medicine, amount, condition} from every active prescription covering this slot.

    Never reads via a prescription FK: a slot can combine items from several
    active prescriptions, and the report must stay readable after any of
    them changes or is deleted.
    """
    statement = (
        select(MedicationItem)
        .join(Prescription, Prescription.id == MedicationItem.prescription_id)
        .where(Prescription.user_id == user_id, Prescription.is_active == True)  # noqa: E712
    )
    result = await session.exec(statement)
    items = result.all()
    return [
        {"medicine": item.medicine, "amount": item.dose_amount, "condition": item.condition}
        for item in items
        if period in item.when_slots
    ]


async def record_intake_report(
    session: AsyncSession,
    data: IntakeReportCreate,
    user_id: UUID,
) -> IntakeReport:
    """Upsert an intake for (user_id, period, date) — create or overwrite, never conflict.

    taken_at: the client-supplied moment if given, else now() (see docs/conventions.md
    — absent taken_at means "just taken", present means the client is recording a
    moment other than now).
    recorded_at: always now() on every write, whether this is a create or an edit.
    snapshot: always rebuilt from the user's current active prescriptions, even on
    edit, since "what's active" may have changed since the row was first written.

    Does not require reminder_config to exist — recording an intake has no
    dependency on it now that "late" is a read-time projection, not stored here.
    """
    statement = select(IntakeReport).where(
        IntakeReport.user_id == user_id,
        IntakeReport.period == data.period,
        IntakeReport.date == data.date,
    )
    result = await session.exec(statement)
    report = result.first()

    now = datetime.now(UTC)
    taken_at = data.taken_at if data.taken_at is not None else now
    snapshot = await _build_snapshot(session, user_id, data.period)

    if report is None:
        report = IntakeReport(
            user_id=user_id,
            period=data.period,
            date=data.date,
            taken_at=taken_at,
            recorded_at=now,
            snapshot=snapshot,
        )
    else:
        report.taken_at = taken_at
        report.recorded_at = now
        report.snapshot = snapshot

    session.add(report)
    await session.commit()
    await session.refresh(report)
    return report


async def get_intake_report(
    session: AsyncSession,
    report_id: UUID,
    user_id: UUID,
) -> IntakeReport | None:
    """Return one intake report by id, scoped to the user."""
    statement = select(IntakeReport).where(
        IntakeReport.id == report_id,
        IntakeReport.user_id == user_id,
    )
    result = await session.exec(statement)
    return result.first()


async def get_intake_reports(
    session: AsyncSession,
    user_id: UUID,
) -> Sequence[IntakeReport]:
    """Return all intake reports belonging to the given user."""
    statement = select(IntakeReport).where(IntakeReport.user_id == user_id)
    result = await session.exec(statement)
    return result.all()
