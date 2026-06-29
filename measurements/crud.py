from collections.abc import Sequence
from uuid import UUID

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from measurements.models import Measurement, MeasurementCreate, MeasurementUpdate


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


async def get_measurement(
    session: AsyncSession,
    measurement_id: UUID,
    user_id: UUID,
) -> Measurement | None:
    """Return one measurement by id, scoped to the user."""
    statement = select(Measurement).where(
        Measurement.id == measurement_id,
        Measurement.user_id == user_id,
    )
    result = await session.exec(statement)
    return result.first()


async def update_measurement(
    session: AsyncSession,
    measurement_id: UUID,
    data: MeasurementUpdate,
    user_id: UUID,
) -> Measurement | None:
    """Apply partial changes to a user's measurement."""
    measurement = await get_measurement(session, measurement_id, user_id)
    if measurement is None:
        return None
    # exclude_unset: only fields the client actually sent get changed.
    updates = data.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(measurement, field, value)
    session.add(measurement)
    await session.commit()
    await session.refresh(measurement)
    return measurement
