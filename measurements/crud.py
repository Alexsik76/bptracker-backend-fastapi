from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlmodel import col, select
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
    days: int = 90,
) -> Sequence[Measurement]:
    """Return a user's measurements from the last N days, newest first."""
    # TODO: extract user-scoping when a 3rd query needs it
    cutoff = datetime.now(UTC) - timedelta(days=days)
    statement = (
        select(Measurement)
        .where(
            Measurement.user_id == user_id,
            col(Measurement.recorded_at) >= cutoff,
        )
        .order_by(col(Measurement.recorded_at).desc())
    )
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


async def delete_measurement(
    session: AsyncSession,
    measurement_id: UUID,
    user_id: UUID,
) -> bool:
    """Delete a user's measurement. Returns False if it doesn't exist."""
    measurement = await get_measurement(session, measurement_id, user_id)
    if measurement is None:
        return False
    await session.delete(measurement)
    await session.commit()
    return True
