from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from prescriptions.models import MedicationItem, Prescription, WhenSlot
from reminders.crud.reminder_config import get_reminder_config
from reminders.models import IntakeReport, IntakeReportCreate

_SLOT_TIME_FIELD: dict[WhenSlot, str] = {
    WhenSlot.MORNING: "morning_time",
    WhenSlot.DAY: "day_time",
    WhenSlot.EVENING: "evening_time",
}


class IntakeReportAlreadyExists(Exception):
    """Raised when (user_id, period, date) already has a confirmed intake."""


async def _build_snapshot(
    session: AsyncSession,
    user_id: UUID,
    period: WhenSlot,
) -> list[dict]:
    """Collect {medicine, amount, condition} from every active prescription covering this slot.

    Never reads via prescription_id: a slot can combine items from several
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


async def create_intake_report(
    session: AsyncSession,
    data: IntakeReportCreate,
    user_id: UUID,
) -> IntakeReport | None:
    """Confirm an intake. Returns None if the user has no reminder_config yet.

    Raises IntakeReportAlreadyExists if this user/period/date was already confirmed.
    """
    config = await get_reminder_config(session, user_id)
    if config is None:
        return None

    confirmed_at = datetime.now(UTC)
    slot_time = getattr(config, _SLOT_TIME_FIELD[data.period])
    window_end = datetime.combine(data.date, slot_time, tzinfo=UTC) + timedelta(
        minutes=config.duration_minutes
    )
    is_late = confirmed_at > window_end

    snapshot = await _build_snapshot(session, user_id, data.period)

    report = IntakeReport(
        user_id=user_id,
        prescription_id=data.prescription_id,
        period=data.period,
        date=data.date,
        confirmed_at=confirmed_at,
        is_late=is_late,
        snapshot=snapshot,
    )
    session.add(report)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise IntakeReportAlreadyExists from None
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
