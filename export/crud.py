from datetime import datetime
from uuid import UUID

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from export.models import ExportState


async def lock_export_state(session: AsyncSession, user_id: UUID) -> ExportState | None:
    """Lock and return the export state for the user if it exists."""
    statement = select(ExportState).where(ExportState.user_id == user_id).with_for_update()
    result = await session.exec(statement)
    return result.first()


async def stamp_export(session: AsyncSession, user_id: UUID, at: datetime) -> ExportState:
    """Insert or update the export state with the given timestamp. Does not commit."""
    statement = select(ExportState).where(ExportState.user_id == user_id)
    result = await session.exec(statement)
    state = result.first()
    if state:
        state.last_export_at = at
    else:
        state = ExportState(user_id=user_id, last_export_at=at)
    session.add(state)
    return state
