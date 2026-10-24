from datetime import date
from uuid import UUID

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from nutrition.models import OffPlanEntry, OffPlanEntryCreate


async def create_off_plan_entry(
    session: AsyncSession,
    user_id: UUID,
    data: OffPlanEntryCreate,
) -> OffPlanEntry:
    """Create a new off-plan entry for the user."""
    entry = OffPlanEntry(
        user_id=user_id,
        date=data.date,
        at=data.at,
        text=data.text.strip(),
        kcal_est=data.kcal_est,
    )
    session.add(entry)
    await session.commit()
    await session.refresh(entry)
    return entry


async def get_off_plan_entries(
    session: AsyncSession,
    user_id: UUID,
    entry_date: date,
) -> list[OffPlanEntry]:
    """Retrieve all off-plan entries for a user on a given date, ordered by 'at' ascending."""
    stmt = (
        select(OffPlanEntry)
        .where(OffPlanEntry.user_id == user_id, OffPlanEntry.date == entry_date)
        .order_by(col(OffPlanEntry.at).asc())
    )
    result = await session.exec(stmt)
    return list(result.all())


async def delete_off_plan_entry(
    session: AsyncSession,
    entry_id: UUID,
    user_id: UUID,
) -> bool:
    """Delete an off-plan entry if it belongs to the user."""
    stmt = select(OffPlanEntry).where(
        OffPlanEntry.id == entry_id,
        OffPlanEntry.user_id == user_id,
    )
    entry = (await session.exec(stmt)).first()
    if entry is None:
        return False
    await session.delete(entry)
    await session.commit()
    return True
