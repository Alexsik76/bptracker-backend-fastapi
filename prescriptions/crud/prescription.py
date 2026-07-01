from collections.abc import Sequence
from uuid import UUID

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from prescriptions.models import Prescription, PrescriptionCreate, PrescriptionUpdate


async def create_prescription(
    session: AsyncSession,
    data: PrescriptionCreate,
    user_id: UUID,
) -> Prescription:
    """Persist a new prescription for the given user."""
    prescription = Prescription.model_validate(data, update={"user_id": user_id})
    session.add(prescription)
    await session.commit()
    await session.refresh(prescription)
    return prescription


async def get_prescriptions(
    session: AsyncSession,
    user_id: UUID,
) -> Sequence[Prescription]:
    """Return all prescriptions belonging to the given user."""
    statement = select(Prescription).where(Prescription.user_id == user_id)
    result = await session.exec(statement)
    return result.all()


async def get_prescription(
    session: AsyncSession,
    prescription_id: UUID,
    user_id: UUID,
) -> Prescription | None:
    """Return one prescription by id, scoped to the user."""
    statement = select(Prescription).where(
        Prescription.id == prescription_id,
        Prescription.user_id == user_id,
    )
    result = await session.exec(statement)
    return result.first()


async def update_prescription(
    session: AsyncSession,
    prescription_id: UUID,
    data: PrescriptionUpdate,
    user_id: UUID,
) -> Prescription | None:
    """Apply partial changes to a user's prescription."""
    prescription = await get_prescription(session, prescription_id, user_id)
    if prescription is None:
        return None
    updates = data.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(prescription, field, value)
    session.add(prescription)
    await session.commit()
    await session.refresh(prescription)
    return prescription


async def delete_prescription(
    session: AsyncSession,
    prescription_id: UUID,
    user_id: UUID,
) -> bool:
    """Delete a user's prescription. DB cascade removes its medication items."""
    prescription = await get_prescription(session, prescription_id, user_id)
    if prescription is None:
        return False
    await session.delete(prescription)
    await session.commit()
    return True
