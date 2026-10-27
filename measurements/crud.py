from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from measurements.models import Measurement, MeasurementCreate, MeasurementUpdate


class MeasurementConflictError(Exception):
    """Raised when a measurement with the given ID already exists and belongs to another user."""


async def create_measurement(
    session: AsyncSession,
    data: MeasurementCreate,
    user_id: UUID,
) -> Measurement:
    """Persist a new measurement for the given user, or return existing if idempotent by id."""
    if data.id is not None:
        statement = select(Measurement).where(Measurement.id == data.id)
        existing = (await session.exec(statement)).first()
        if existing is not None:
            if existing.user_id == user_id:
                return existing
            raise MeasurementConflictError("Measurement with this ID belongs to another user")

    measurement = Measurement.model_validate(data, update={"user_id": user_id})
    session.add(measurement)
    try:
        await session.commit()
    except IntegrityError as exc:
        if data.id is not None:
            await session.rollback()
            statement = select(Measurement).where(Measurement.id == data.id)
            existing = (await session.exec(statement)).first()
            if existing is not None:
                if existing.user_id == user_id:
                    return existing
                raise MeasurementConflictError(
                    "Measurement with this ID belongs to another user"
                ) from exc
        raise
    await session.refresh(measurement)
    return measurement


async def get_measurements(
    session: AsyncSession,
    user_id: UUID,
    *,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[Sequence[Measurement], int]:
    """Return a page of a user's measurements, newest first, plus the total count.

    `date_from` is inclusive, `date_to` is exclusive. Both are optional and
    independent; omitting both returns the user's full history. Values are
    compared as-received against `recorded_at` with no server-side timezone
    conversion. `total` reflects the filter only, before `limit`/`offset` are
    applied.
    """
    # TODO: extract user-scoping when a 3rd query needs it
    filters = [Measurement.user_id == user_id]
    if date_from is not None:
        filters.append(col(Measurement.recorded_at) >= date_from)
    if date_to is not None:
        filters.append(col(Measurement.recorded_at) < date_to)

    count_statement = select(func.count()).select_from(Measurement).where(*filters)
    total = (await session.exec(count_statement)).one()

    statement = (
        select(Measurement)
        .where(*filters)
        .order_by(col(Measurement.recorded_at).desc())
        .limit(limit)
        .offset(offset)
    )
    result = await session.exec(statement)
    return result.all(), total


async def get_measurements_for_export(
    session: AsyncSession,
    user_id: UUID,
    *,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> Sequence[Measurement]:
    """Return all measurements for a user in the given half-open range, ordered ascending."""
    filters = [Measurement.user_id == user_id]
    if date_from is not None:
        filters.append(col(Measurement.recorded_at) >= date_from)
    if date_to is not None:
        filters.append(col(Measurement.recorded_at) < date_to)

    statement = (
        select(Measurement)
        .where(*filters)
        .order_by(col(Measurement.recorded_at).asc())
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
