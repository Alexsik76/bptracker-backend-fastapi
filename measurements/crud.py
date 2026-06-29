from collections.abc import Sequence
from uuid import UUID

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from measurements.models import Measurement, MeasurementCreate


async def create_measurement(
    session: AsyncSession,
    data: MeasurementCreate,
    user_id: UUID,
) -> Measurement:
    """Persist a new measurement for the given user."""
    measurement = Measurement.model_validate(data, update={"user_id": user_id})
    session.add(measurement)
    await session.commit()
    # Refresh to load DB-generated values (id, recorded_at) into the object.
    await session.refresh(measurement)
    return measurement


async def get_measurements(
    session: AsyncSession,
    user_id: UUID,
) -> Sequence[Measurement]:
    """Return all measurements belonging to the given user."""
    # TODO: extract user-scoping when a 3rd query needs it
    statement = select(Measurement).where(Measurement.user_id == user_id)
    result = await session.exec(statement)
    return result.all()