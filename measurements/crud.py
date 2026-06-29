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
    # Translate the input form into the storage form, injecting user_id from
    # the request context — never from the client. This single call is the
    # "copy between forms" we discussed: Create (no user_id) -> Measurement (with it).
    measurement = Measurement.model_validate(data, update={"user_id": user_id})

    # Hand the object to the session and persist it.
    session.add(measurement)
    await session.commit()

    # After commit the row exists; refresh pulls DB-generated values
    # (id from uuidv7(), recorded_at from now()) back into the object,
    # so the caller gets a fully-populated Measurement to return.
    await session.refresh(measurement)
    return measurement


async def get_measurements(
    session: AsyncSession,
    user_id: UUID,
) -> Sequence[Measurement]:
    # Read only this user's rows. user_id comes from the request context,
    # so one user can never read another's measurements — the filter is the
    # data-isolation boundary, enforced here, not trusted from the client.
    # TODO: extract user-scoping when a 3rd query needs it
    statement = select(Measurement).where(Measurement.user_id == user_id)
    result = await session.exec(statement)
    return result.all()
